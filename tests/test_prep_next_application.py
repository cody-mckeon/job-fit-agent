from pathlib import Path

import json

from job_fit_agent.config import NotificationConfig, TelegramConfig
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
    assert "Resume PDF: skipped in GitHub Actions" in sent["text"]
    assert "Submit resume markdown: applications/acme/submit_resume.md" in sent["text"]
    assert "PDF export failed or skipped. Review submit_resume.md instead." in sent["text"]


def test_scheduled_workflow_runs_prep_next_with_skip_browser_and_notify():
    workflow = Path('.github/workflows/job-agent.yml').read_text()
    assert "python -m job_fit_agent.main prep-next-application --skip-browser --skip-pdf --notify-telegram" in workflow


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
