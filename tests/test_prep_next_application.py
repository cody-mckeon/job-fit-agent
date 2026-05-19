import json
from pathlib import Path

from job_fit_agent.main import prep_next_application


def _job(job_id: int, **overrides):
    row = {
        "id": job_id,
        "company": "acme",
        "title": "Product Manager",
        "url": f"https://example.com/{job_id}",
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
            _job(5),
        ],
    )
    prep_next_application(dry_run=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 5


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
