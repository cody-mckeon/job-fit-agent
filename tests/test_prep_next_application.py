from pathlib import Path

import json

from job_fit_agent.config import NotificationConfig, TelegramConfig
from job_fit_agent import main as job_main
from job_fit_agent.main import main, prep_next_application


def _job(job_id: int, **overrides):
    row = {
        "id": job_id,
        "company": "acme",
        "title": "Product Manager",
        "url": f"https://jobs.ashbyhq.com/acme/{job_id}",
        "score": 80,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "geographic_eligibility": "eligible",
        "status": "new",
    }
    row.update(overrides)
    return row


def test_dry_run_does_not_create_files_or_change_status(monkeypatch, capsys):
    selected = _job(1)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [selected])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: (_ for _ in ()).throw(AssertionError("should not prep")))
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: (_ for _ in ()).throw(AssertionError("should not export")))
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: (_ for _ in ()).throw(AssertionError("should not update")))

    prep_next_application(dry_run=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 1
    assert payload["next_action"] == "review selected job"


def test_auto_select_chooses_highest_ranked_actionable_job(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main._get_prep_next_application_candidates",
        lambda: [
            _job(10, classification="near_fit", score=99),
            _job(20, classification="high_fit", viability_level="review", score=40),
            _job(30, classification="high_fit", viability_level="apply_now", score=60),
        ],
    )
    prep_next_application(dry_run=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 30


def test_skip_and_excluded_status_jobs_are_excluded(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main._get_prep_next_application_candidates",
        lambda: [
            _job(1, viability_level="skip"),
            _job(2, status="applied"),
            _job(3, status="rejected"),
            _job(4, status="archived"),
            _job(5, geographic_eligibility="ineligible"),
            _job(6, classification="low_fit"),
            _job(7),
        ],
    )
    prep_next_application(dry_run=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 7


def test_job_id_prepares_specific_job(monkeypatch, capsys):
    job = _job(42)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job if job_id == 42 else None)
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.extract_application_questions_browser", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.generate_application_answers", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(job_id=42, skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 42




def test_job_id_low_fit_blocked_without_force(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: _job(34, classification="low_fit") if job_id == 34 else None)

    result = prep_next_application(job_id=34, skip_browser=True)
    output = capsys.readouterr().out
    assert result is None
    assert "Job is not actionable. Use --force to prepare anyway." in output


def test_job_id_skip_blocked_without_force(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: _job(35, viability_level="skip") if job_id == 35 else None)

    result = prep_next_application(job_id=35, skip_browser=True)
    output = capsys.readouterr().out
    assert result is None
    assert "Job is not actionable. Use --force to prepare anyway." in output


def test_job_id_ineligible_blocked_without_force(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: _job(36, geographic_eligibility="ineligible") if job_id == 36 else None)

    result = prep_next_application(job_id=36, skip_browser=True)
    output = capsys.readouterr().out
    assert result is None
    assert "Job is not actionable. Use --force to prepare anyway." in output


def test_force_allows_non_actionable_job_id(monkeypatch, capsys):
    job = _job(37, viability_level="skip")
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job if job_id == 37 else None)
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(job_id=37, skip_browser=True, force=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 37
    assert payload["warning"] == "Prepared despite non-actionable status because --force was used."
    assert payload["actionable"] is False

def test_skip_browser_skips_extraction(monkeypatch, capsys):
    job = _job(7)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.setattr("job_fit_agent.main.extract_application_questions_browser", lambda job_id: (_ for _ in ()).throw(AssertionError("should skip")))

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert "application_questions_path" not in payload


def test_skip_pdf_does_not_call_export_resume_pdf(monkeypatch, capsys):
    job = _job(8)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.setattr(
        "job_fit_agent.main.export_resume_pdf",
        lambda job_id: (_ for _ in ()).throw(AssertionError("should skip pdf export")),
    )

    prep_next_application(skip_browser=True, skip_pdf=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["pdf_skipped"] is True
    assert payload["resume_pdf_path"] is None


def test_missing_pandoc_does_not_crash(monkeypatch, capsys):
    job = _job(18)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr(
        "job_fit_agent.main.export_resume_pdf",
        lambda job_id: (_ for _ in ()).throw(FileNotFoundError(2, "No such file or directory", "pandoc")),
    )
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["pdf_export"] == "failed"
    assert "PDF export failed: pandoc not found" in payload["warnings"]


def test_json_summary_includes_required_paths(monkeypatch, capsys):
    job = _job(9)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.extract_application_questions_browser", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.generate_application_answers", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["application_folder"]
    assert payload["resume_pdf_path"].endswith(".pdf")
    assert payload["pdf_skipped"] is False
    assert payload["cover_letter_path"].endswith("cover_letter.md")
    assert payload["recruiter_note_path"].endswith("recruiter_note.md")
    assert payload["risk_flags_path"].endswith("risk_flags.md")


def test_json_summary_includes_github_actions_run_url_when_env_present(monkeypatch, capsys):
    job = _job(19)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "cody-mckeon/job-fit-agent")
    monkeypatch.setenv("GITHUB_RUN_ID", "123456789")

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["github_actions_run_url"] == "https://github.com/cody-mckeon/job-fit-agent/actions/runs/123456789"


def test_json_summary_omits_github_actions_run_url_when_env_missing(monkeypatch, capsys):
    job = _job(21)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert "github_actions_run_url" not in payload


def test_browser_extraction_failure_returns_warning_but_succeeds(monkeypatch, capsys, tmp_path):
    job = _job(11)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.extract_application_questions_browser", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application()
    payload = json.loads(capsys.readouterr().out)
    assert payload["warning"] == "application question extraction failed; inspect manually"
    assert payload["job_id"] == 11


def test_notify_telegram_sends_when_credentials_exist(monkeypatch):
    sent: dict[str, str] = {}
    summary = _job(12) | {
        "application_folder": "applications/acme_product_manager_12",
        "resume_pdf_path": "applications/acme_product_manager_12/resume.pdf",
        "cover_letter_path": "applications/acme_product_manager_12/cover_letter.md",
        "risk_flags_path": "applications/acme_product_manager_12/risk_flags.md",
        "application_answers_path": "applications/acme_product_manager_12/application_answers.md",
    }
    monkeypatch.setattr("job_fit_agent.main.prep_next_application", lambda **kwargs: summary)
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda text, bot_token, chat_id: sent.update({"text": text, "bot_token": bot_token, "chat_id": chat_id}),
    )

    main(["prep-next-application", "--notify-telegram"])
    assert sent["bot_token"] == "token"
    assert sent["chat_id"] == "chat"
    assert "Job title: Product Manager" in sent["text"]
    assert "Company: acme" in sent["text"]
    assert "Job URL: https://jobs.ashbyhq.com/acme/12" in sent["text"]
    assert "Cover letter: applications/acme_product_manager_12/cover_letter.md" in sent["text"]
    assert "Risk flags: applications/acme_product_manager_12/risk_flags.md" in sent["text"]
    assert "Next action: Review materials manually before submitting." in sent["text"]
    assert "Application answers: applications/acme_product_manager_12/application_answers.md" in sent["text"]


def test_notify_telegram_missing_credentials_skips_without_failing(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.prep_next_application", lambda **kwargs: _job(13))
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="", chat_id="")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )
    main(["prep-next-application", "--notify-telegram"])
    assert "Telegram notification skipped: missing credentials" in capsys.readouterr().out


def test_notify_telegram_includes_browser_extraction_warning(monkeypatch):
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        "job_fit_agent.main.prep_next_application",
        lambda **kwargs: _job(14)
        | {
            "application_folder": "applications/acme",
            "resume_pdf_path": "applications/acme/resume.pdf",
            "cover_letter_path": "applications/acme/cover_letter.md",
            "risk_flags_path": "applications/acme/risk_flags.md",
            "warning": "application question extraction failed; inspect manually",
        },
    )
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda text, bot_token, chat_id: sent.update({"text": text}),
    )
    main(["prep-next-application", "--notify-telegram"])
    assert "Browser extraction failed: inspect manually." in sent["text"]


def test_notify_telegram_includes_skip_browser_warning(monkeypatch):
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        "job_fit_agent.main.prep_next_application",
        lambda **kwargs: _job(15)
        | {
            "application_folder": "applications/acme",
            "resume_pdf_path": "applications/acme/resume.pdf",
            "cover_letter_path": "applications/acme/cover_letter.md",
            "risk_flags_path": "applications/acme/risk_flags.md",
            "skip_browser": True,
            "pdf_export": "generated",
        },
    )
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda text, bot_token, chat_id: sent.update({"text": text}),
    )
    main(["prep-next-application", "--notify-telegram"])
    assert "Browser extraction skipped (--skip-browser)." in sent["text"]


def test_notify_telegram_includes_pdf_skipped_warning(monkeypatch):
    sent: dict[str, str] = {}
    monkeypatch.setattr(
        "job_fit_agent.main.prep_next_application",
        lambda **kwargs: _job(16)
        | {
            "application_folder": "applications/acme",
            "submit_resume_path": "applications/acme/submit_resume.md",
            "resume_pdf_path": None,
            "cover_letter_path": "applications/acme/cover_letter.md",
            "risk_flags_path": "applications/acme/risk_flags.md",
            "skip_browser": True,
            "pdf_skipped": True,
            "pdf_export": "skipped",
        },
    )
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda text, bot_token, chat_id: sent.update({"text": text}),
    )
    main(["prep-next-application", "--notify-telegram"])
    assert "Resume PDF: failed or skipped, use submit_resume.md" in sent["text"]
    assert "Submit resume markdown: applications/acme/submit_resume.md" in sent["text"]
    assert "PDF export failed or skipped. Review submit_resume.md instead." in sent["text"]




def test_prep_next_application_creates_package_zip(monkeypatch, capsys, tmp_path):
    job = _job(71)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])

    def _prep(job_id):
        app_dir = Path("applications/acme_product_manager_71")
        app_dir.mkdir(parents=True, exist_ok=True)
        for name in ["fit_summary.md", "resume_strategy.md", "resume_draft.md", "submit_resume.md", "recruiter_note.md", "answer_bank.md", "risk_flags.md", "cover_letter.md"]:
            (app_dir / name).write_text("x", encoding="utf-8")

    monkeypatch.setattr("job_fit_agent.main.prep_application", _prep)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["package_zip_created"] is True
    assert Path(payload["package_zip_path"]).exists()


def test_package_zip_contains_expected_files(monkeypatch, capsys, tmp_path):
    import zipfile

    job = _job(72)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])

    def _prep(job_id):
        app_dir = Path("applications/acme_product_manager_72")
        app_dir.mkdir(parents=True, exist_ok=True)
        for name in ["fit_summary.md", "resume_strategy.md", "resume_draft.md", "submit_resume.md", "recruiter_note.md", "answer_bank.md", "risk_flags.md", "cover_letter.md"]:
            (app_dir / name).write_text("x", encoding="utf-8")

    monkeypatch.setattr("job_fit_agent.main.prep_application", _prep)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    with zipfile.ZipFile(payload["package_zip_path"]) as zf:
        names = set(zf.namelist())
    assert "applications/acme_product_manager_72/fit_summary.md" in names
    assert "applications/acme_product_manager_72/cover_letter.md" in names


def test_notify_telegram_uploads_document_when_zip_exists(monkeypatch):
    sent = {"messages": 0, "docs": 0}
    monkeypatch.setattr("job_fit_agent.main.prep_next_application", lambda **kwargs: _job(73) | {"package_zip_created": True, "package_zip_path": "applications/acme.zip"})
    monkeypatch.setattr("job_fit_agent.main.load_notification_config", lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")))
    monkeypatch.setattr("job_fit_agent.main.send_message_with_credentials", lambda **kwargs: sent.__setitem__("messages", sent["messages"] + 1))
    monkeypatch.setattr("job_fit_agent.main.send_document_with_credentials", lambda **kwargs: sent.__setitem__("docs", sent["docs"] + 1))
    main(["prep-next-application", "--notify-telegram"])
    assert sent["messages"] == 1
    assert sent["docs"] == 1


def test_notify_telegram_text_still_sends_when_document_upload_fails(monkeypatch, capsys):
    sent = {"messages": 0}
    monkeypatch.setattr("job_fit_agent.main.prep_next_application", lambda **kwargs: _job(74) | {"package_zip_created": True, "package_zip_path": "applications/acme.zip"})
    monkeypatch.setattr("job_fit_agent.main.load_notification_config", lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")))
    monkeypatch.setattr("job_fit_agent.main.send_message_with_credentials", lambda **kwargs: sent.__setitem__("messages", sent["messages"] + 1))
    monkeypatch.setattr("job_fit_agent.main.send_document_with_credentials", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    main(["prep-next-application", "--notify-telegram"])
    assert sent["messages"] == 1
    assert "Telegram package upload failed" in capsys.readouterr().out


def test_prep_next_application_does_not_crash_when_zip_creation_fails(monkeypatch, capsys):
    job = _job(75)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._get_prep_next_application_candidates", lambda: [job])
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main._create_application_package_zip", lambda app_dir: (_ for _ in ()).throw(RuntimeError("zip fail")))
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    assert "Package zip creation failed" in payload["warnings"]
def test_scheduled_workflow_runs_prep_next_with_skip_browser_and_notify():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert "python -m job_fit_agent.main prep-next-application --skip-browser --notify-telegram" in workflow
    assert "--skip-pdf" not in workflow


def test_scheduled_workflow_installs_pdf_dependencies():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert 'name: Install PDF export dependencies' in workflow
    assert 'sudo apt-get update' in workflow
    assert 'sudo apt-get install -y pandoc texlive-latex-base texlive-latex-recommended texlive-fonts-recommended lmodern' in workflow


def test_scheduled_workflow_uploads_application_artifact():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert 'uses: actions/upload-artifact@v4' in workflow
    assert 'path: applications/' in workflow
    assert 'retention-days: 14' in workflow
    assert 'if-no-files-found: warn' in workflow


def test_telegram_message_mentions_actions_artifact(monkeypatch):
    sent = {}
    monkeypatch.setattr('job_fit_agent.main.prep_next_application', lambda **kwargs: {
        'company': 'Acme',
        'title': 'PM',
        'score': 92,
        'classification': 'priority',
        'viability_level': 'apply_now',
        'geographic_eligibility': 'eligible',
        'url': 'https://example.org/job/1',
        'application_folder': 'applications/acme',
        'submit_resume_path': 'applications/acme/submit_resume.md',
        'resume_pdf_path': None,
        'pdf_skipped': True,
        'cover_letter_path': 'applications/acme/cover_letter.md',
        'recruiter_note_path': 'applications/acme/recruiter_note.md',
        'risk_flags_path': 'applications/acme/risk_flags.md',
        'reasons': [],
        'viability_reasons': [],
        'red_flags': [],
        'skip_browser': True,
        'pdf_export': 'skipped',
    })
    monkeypatch.setattr(
        'job_fit_agent.main.load_notification_config',
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token='token', chat_id='chat')),
    )
    monkeypatch.setattr(
        'job_fit_agent.main.send_message_with_credentials',
        lambda text, bot_token, chat_id: sent.update({'text': text}),
    )
    main(['prep-next-application', '--notify-telegram'])
    assert "Generated files are available in this run's artifact" in sent['text']
    assert 'If resume PDF export fails, use submit_resume.md for manual submission.' in sent['text']


def test_telegram_message_includes_github_actions_run_url_when_present(monkeypatch):
    sent = {}
    monkeypatch.setattr('job_fit_agent.main.prep_next_application', lambda **kwargs: {
        'company': 'Acme',
        'title': 'PM',
        'score': 92,
        'classification': 'priority',
        'viability_level': 'apply_now',
        'geographic_eligibility': 'eligible',
        'url': 'https://example.org/job/1',
        'application_folder': 'applications/acme',
        'submit_resume_path': 'applications/acme/submit_resume.md',
        'resume_pdf_path': None,
        'pdf_skipped': True,
        'cover_letter_path': 'applications/acme/cover_letter.md',
        'recruiter_note_path': 'applications/acme/recruiter_note.md',
        'risk_flags_path': 'applications/acme/risk_flags.md',
        'reasons': [],
        'viability_reasons': [],
        'red_flags': [],
        'skip_browser': True,
        'pdf_export': 'skipped',
        'github_actions_run_url': 'https://github.com/cody-mckeon/job-fit-agent/actions/runs/123456789',
    })
    monkeypatch.setattr(
        'job_fit_agent.main.load_notification_config',
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token='token', chat_id='chat')),
    )
    monkeypatch.setattr(
        'job_fit_agent.main.send_message_with_credentials',
        lambda text, bot_token, chat_id: sent.update({'text': text}),
    )
    main(['prep-next-application', '--notify-telegram'])
    assert 'GitHub Actions run: https://github.com/cody-mckeon/job-fit-agent/actions/runs/123456789' in sent['text']
    assert 'Backup: GitHub Actions artifact available in workflow run.' in sent['text']


def test_scheduled_workflow_does_not_commit_jobs_sqlite():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert "git add data/jobs.sqlite" not in workflow
    assert "git commit" not in workflow


def test_job_id_placeholder_url_blocked_without_force(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: _job(55, url="https://example.com/fake"))
    result = prep_next_application(job_id=55, skip_browser=True)
    assert result is None
    assert "Job is not actionable. Use --force to prepare anyway." in capsys.readouterr().out


def test_job_id_placeholder_url_allowed_with_force(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: _job(56, url="https://example.com/fake"))
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    prep_next_application(job_id=56, skip_browser=True, force=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 56


def test_notify_telegram_no_summary_prints_real_url_message(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.prep_next_application", lambda **kwargs: None)
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token="token", chat_id="chat")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.send_message_with_credentials",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not send")),
    )
    main(["prep-next-application", "--notify-telegram"])
    assert "No actionable real job URL found." in capsys.readouterr().out


def test_scheduled_workflow_clears_runtime_db_after_pytest():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert "Run tests" in workflow
    assert "rm -f data/jobs.sqlite data/jobs.sqlite-shm data/jobs.sqlite-wal" in workflow


def test_scheduled_workflow_pytest_step_does_not_expose_telegram_secrets():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert 'name: Run tests' in workflow
    assert 'TELEGRAM_BOT_TOKEN: ""' in workflow
    assert 'TELEGRAM_CHAT_ID: ""' in workflow


def test_scheduled_workflow_prep_step_has_telegram_secrets_only():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    prep_idx = workflow.index('name: Prep next application package and notify Telegram')
    run_tests_idx = workflow.index('name: Run tests')
    pytest_block = workflow[run_tests_idx:prep_idx]
    assert '${{ secrets.TELEGRAM_BOT_TOKEN }}' not in pytest_block
    assert '${{ secrets.TELEGRAM_CHAT_ID }}' not in pytest_block
    prep_block = workflow[prep_idx:]
    assert '${{ secrets.TELEGRAM_BOT_TOKEN }}' in prep_block
    assert '${{ secrets.TELEGRAM_CHAT_ID }}' in prep_block


def test_notify_telegram_shows_resume_pdf_included_when_present(monkeypatch):
    sent = {}
    monkeypatch.setattr(
        'job_fit_agent.main.prep_next_application',
        lambda **kwargs: {
            'company': 'Acme',
            'title': 'PM',
            'score': 92,
            'classification': 'priority',
            'viability_level': 'apply_now',
            'geographic_eligibility': 'eligible',
            'url': 'https://example.org/job/1',
            'application_folder': 'applications/acme',
            'submit_resume_path': 'applications/acme/submit_resume.md',
            'resume_pdf_path': 'applications/acme/Cody_McKeon_Acme_PM_Resume.pdf',
            'cover_letter_path': 'applications/acme/cover_letter.md',
            'recruiter_note_path': 'applications/acme/recruiter_note.md',
            'risk_flags_path': 'applications/acme/risk_flags.md',
            'reasons': [],
            'viability_reasons': [],
            'red_flags': [],
            'skip_browser': True,
            'pdf_export': 'generated',
        },
    )
    monkeypatch.setattr(
        'job_fit_agent.main.load_notification_config',
        lambda: NotificationConfig(telegram=TelegramConfig(bot_token='token', chat_id='chat')),
    )
    monkeypatch.setattr(
        'job_fit_agent.main.send_message_with_credentials',
        lambda text, bot_token, chat_id: sent.update({'text': text}),
    )
    main(['prep-next-application', '--notify-telegram'])
    assert 'Resume PDF: included' in sent['text']


def test_package_zip_contains_pdf_when_resume_pdf_exists(monkeypatch, capsys, tmp_path):
    import zipfile

    job = _job(76)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('job_fit_agent.main.initialize', lambda: None)
    monkeypatch.setattr('job_fit_agent.main._get_prep_next_application_candidates', lambda: [job])

    def _prep(job_id):
        app_dir = Path('applications/acme_product_manager_76')
        app_dir.mkdir(parents=True, exist_ok=True)
        for name in ['fit_summary.md', 'submit_resume.md', 'cover_letter.md']:
            (app_dir / name).write_text('x', encoding='utf-8')

    def _export(job_id):
        app_dir = Path('applications/acme_product_manager_76')
        (app_dir / 'Cody_McKeon_acme_Product_Manager_Resume.pdf').write_bytes(b'%PDF-1.4')

    monkeypatch.setattr('job_fit_agent.main.prep_application', _prep)
    monkeypatch.setattr('job_fit_agent.main.export_resume_pdf', _export)
    monkeypatch.setattr('job_fit_agent.main.update_status', lambda job_id, status: None)

    prep_next_application(skip_browser=True)
    payload = json.loads(capsys.readouterr().out)
    with zipfile.ZipFile(payload['package_zip_path']) as zf:
        names = set(zf.namelist())
    assert 'applications/acme_product_manager_76/Cody_McKeon_acme_Product_Manager_Resume.pdf' in names


def test_forward_deployed_remote_us_is_auto_prep_eligible():
    job = _job(
        901,
        title="Forward Deployed Engineer, AI",
        location="Remote US",
        classification="high_fit",
        viability_level="apply_now",
        geographic_eligibility="eligible",
    )
    assert job_main._is_prep_next_application_eligible(job) is True


def test_forward_deployed_dach_is_high_fit_but_not_auto_prep_eligible():
    job = _job(
        902,
        title="Forward Deployed Engineer, GTM, DACH",
        location="Remote",
        classification="high_fit",
        viability_level="apply_now",
        geographic_eligibility="review",
        red_flags='["DACH region role may not be US eligible"]',
    )
    assert job_main._is_prep_next_application_eligible(job) is False


def test_forward_deployed_new_york_onsite_is_not_auto_prep_eligible():
    job = _job(
        903,
        title="Forward Deployed Engineer, AI",
        location="New York, NY",
        classification="high_fit",
        viability_level="apply_now",
        geographic_eligibility="ineligible",
    )
    assert job_main._is_prep_next_application_eligible(job) is False


def test_forward_deployed_emea_is_not_auto_prep_eligible():
    job = _job(
        904,
        title="Forward Deployed Engineer, EMEA",
        location="Remote",
        classification="high_fit",
        viability_level="apply_now",
        geographic_eligibility="review",
    )
    assert job_main._is_prep_next_application_eligible(job) is False


def test_product_manager_remote_us_still_auto_prep_eligible():
    job = _job(905, title="Product Manager", location="Remote US")
    assert job_main._is_prep_next_application_eligible(job) is True


def test_high_score_geo_review_is_excluded_from_auto_selection():
    job = _job(906, score=100, geographic_eligibility="review")
    assert job_main._is_prep_next_application_eligible(job) is False


def test_force_allows_explicit_geography_review_job(monkeypatch, capsys):
    job = _job(907, geographic_eligibility="review")
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job if job_id == 907 else None)
    monkeypatch.setattr("job_fit_agent.main.prep_application", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.export_resume_pdf", lambda job_id: None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    prep_next_application(job_id=907, skip_browser=True, force=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 907
    assert payload["actionable"] is False
    assert "Geography requires manual review before applying." in payload["warnings"]


def test_telegram_summary_includes_mobile_alias_command():
    message = job_main._format_prep_next_application_telegram_message(
        _job(19, company="linear", title="Product Manager")
        | {
            "mobile_command_alias": "linear-product-manager",
            "stable_job_key": "ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2",
        }
    )

    assert """After applying:
```
applied linear-product-manager
```""" in message
    assert """To skip:
```
skip linear-product-manager Not a fit
```""" in message
    assert """To save:
```
save linear-product-manager
```""" in message


def test_telegram_summary_includes_stable_fallback_command():
    message = job_main._format_prep_next_application_telegram_message(
        _job(19, company="linear", title="Product Manager")
        | {
            "mobile_command_alias": "linear-product-manager",
            "stable_job_key": "ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2",
        }
    )

    assert """Stable fallback:
```
applied ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2
```""" in message
