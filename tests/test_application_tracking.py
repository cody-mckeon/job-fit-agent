import json

from job_fit_agent import main as job_main
from job_fit_agent.main import main, prep_next_application
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_job_by_id, get_job_by_url, initialize, update_application_tracking, upsert_job


def _job(url: str, title: str = "Product Manager", geo: str = "eligible") -> JobPosting:
    return JobPosting(
        source="ashby",
        company="acme",
        title=title,
        location="Remote US",
        location_raw="Remote US",
        geographic_eligibility=geo,
        url=url,
        description="AI workflow automation product systems",
    )


def _fit(score: int = 90, classification: str = "high_fit", viability: str = "apply_now") -> FitScore:
    return FitScore(
        total_score=score,
        classification=classification,
        role_family="product",
        viability_score=90,
        viability_level=viability,
        reasons=["strong product fit"],
        red_flags=[],
    )


def _insert(url: str, title: str = "Product Manager", *, geo: str = "eligible", classification: str = "high_fit") -> int:
    upsert_job(_job(url, title=title, geo=geo), _fit(classification=classification))
    row = get_job_by_url(url)
    assert row is not None
    return int(row["id"])


def test_unapplied_high_fit_lists_high_fit_jobs_that_are_not_applied(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/acme/unapplied", "Unapplied PM")

    main(["unapplied-high-fit"])

    output = capsys.readouterr().out
    assert "Unapplied PM" in output
    assert "https://jobs.ashbyhq.com/acme/unapplied" in output


def test_unapplied_high_fit_excludes_applied_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/applied", "Applied PM")
    update_application_tracking(job_id, "applied", applied_at="2026-05-28T00:00:00+00:00")

    main(["unapplied-high-fit"])

    assert "Applied PM" not in capsys.readouterr().out


def test_unapplied_high_fit_excludes_skipped_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/skipped", "Skipped PM")
    update_application_tracking(job_id, "skipped", skipped_at="2026-05-28T00:00:00+00:00")

    main(["unapplied-high-fit"])

    assert "Skipped PM" not in capsys.readouterr().out


def test_mark_applied_updates_application_status_and_applied_at_by_job_id(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/mark-id", "Mark ID PM")

    main(["mark-applied", "--job-id", str(job_id), "--note", "Applied through Ashby."])

    row = get_job_by_id(job_id)
    assert row is not None
    assert row["application_status"] == "applied"
    assert row["applied_at"]
    assert row["application_notes"] == "Applied through Ashby."
    assert "Marked applied" in capsys.readouterr().out


def test_mark_applied_works_by_url(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/mark-url", "Mark URL PM")

    main(["mark-applied", "--url", "https://jobs.ashbyhq.com/acme/mark-url"])

    row = get_job_by_id(job_id)
    assert row is not None
    assert row["application_status"] == "applied"
    assert row["applied_at"]


def test_prep_next_application_does_not_select_applied_jobs(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main._get_prep_next_application_candidates",
        lambda: [
            {
                "id": 1,
                "company": "acme",
                "title": "Applied PM",
                "url": "https://jobs.ashbyhq.com/acme/applied-prep",
                "score": 99,
                "classification": "high_fit",
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
                "status": "new",
                "application_status": "applied",
            },
            {
                "id": 2,
                "company": "acme",
                "title": "Unapplied PM",
                "url": "https://jobs.ashbyhq.com/acme/unapplied-prep",
                "score": 90,
                "classification": "high_fit",
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
                "status": "new",
                "application_status": "not_applied",
            },
        ],
    )

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == 2


def test_digest_excludes_applied_jobs_from_actionable_sections(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=50: [
            {"id": 1, "score": 95, "status": "new", "application_status": "applied", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Applied PM", "company": "a", "source": "ashby", "url": "https://jobs.ashbyhq.com/acme/digest-applied", "viability_reasons": "[]", "red_flags": "[]"},
            {"id": 2, "score": 90, "status": "new", "application_status": "not_applied", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Open PM", "company": "a", "source": "ashby", "url": "https://jobs.ashbyhq.com/acme/digest-open", "viability_reasons": "[]", "red_flags": "[]"},
        ] if classification == "high_fit" else [],
    )

    main(["digest"])

    output = capsys.readouterr().out
    actionable = output.split("Actionable near-fit jobs")[0]
    assert "Applied PM" not in actionable
    assert "Open PM" in actionable
    assert "applied_count: 1" in output


def test_geography_review_jobs_appear_only_in_review_section_unless_eligible_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/acme/review", "Review PM", geo="review")
    _insert("https://jobs.ashbyhq.com/acme/eligible", "Eligible PM", geo="eligible")

    main(["unapplied-high-fit"])
    output = capsys.readouterr().out
    eligible_section = output.split("Unapplied high-fit jobs needing geography review")[0]
    assert "Eligible PM" in eligible_section
    assert "Review PM" not in eligible_section
    assert "Review PM" in output

    main(["unapplied-high-fit", "--eligible-only"])
    eligible_only_output = capsys.readouterr().out
    assert "Eligible PM" in eligible_only_output
    assert "Review PM" not in eligible_only_output


def test_applied_command_lists_applied_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/list-applied", "Listed Applied PM")
    update_application_tracking(job_id, "applied", applied_at="2026-05-28T00:00:00+00:00", application_notes="Submitted.")

    main(["applied"])

    output = capsys.readouterr().out
    assert "Listed Applied PM" in output
    assert "2026-05-28T00:00:00+00:00" in output
    assert "Submitted." in output


def test_mark_applied_by_stable_key_updates_sqlite_and_status_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/stable-exists", "Stable Exists PM")

    main(["telegram-command", "applied ashby:acme:stable-exists"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    assert result["success"] is True
    assert result["warning"] is None
    assert result["message"] == "Marked applied: acme Stable Exists PM."
    assert row is not None
    assert row["application_status"] == "applied"
    assert store["ashby:acme:stable-exists"]["title"] == "Stable Exists PM"
    assert store["ashby:acme:stable-exists"]["score"] == 90


def test_mark_applied_by_stable_key_without_sqlite_job_creates_status_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["telegram-command", "applied ashby:elevenlabs:a3097257-a07a-4a7e-b9fe-b8555c1a0fa7"])

    result = json.loads(capsys.readouterr().out)
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    record = store["ashby:elevenlabs:a3097257-a07a-4a7e-b9fe-b8555c1a0fa7"]
    assert result["success"] is True
    assert result["warning"] == "Job was not found in local SQLite, but status was recorded by stable key."
    assert result["message"] == "Marked applied by stable key: ashby:elevenlabs:a3097257-a07a-4a7e-b9fe-b8555c1a0fa7. Job details will be enriched when rediscovered."
    assert record["application_status"] == "applied"
    assert record["company"] == "elevenlabs"
    assert record["source"] == "ashby"
    assert record["external_job_id"] == "a3097257-a07a-4a7e-b9fe-b8555c1a0fa7"
    assert record["url"] == "https://jobs.ashbyhq.com/elevenlabs/a3097257-a07a-4a7e-b9fe-b8555c1a0fa7"


def test_application_status_json_is_created_if_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    assert not (tmp_path / "data/application_status.json").exists()

    main(["telegram-command", "applied ashby:acme:missing-store"])

    assert json.loads(capsys.readouterr().out)["success"] is True
    assert (tmp_path / "data/application_status.json").exists()


def test_applied_report_includes_status_only_records(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/application_status.json").write_text(json.dumps({
        "ashby:elevenlabs:status-only": {
            "stable_job_key": "ashby:elevenlabs:status-only",
            "company": "elevenlabs",
            "title": "Unknown",
            "url": "https://jobs.ashbyhq.com/elevenlabs/status-only",
            "source": "ashby",
            "external_job_id": "status-only",
            "application_status": "applied",
            "applied_at": "2026-05-28T00:00:00+00:00",
            "skipped_at": None,
            "saved_at": None,
            "note": "Submitted remotely.",
            "updated_at": "2026-05-28T00:00:00+00:00",
            "identifier_used": "ashby:elevenlabs:status-only",
        }
    }))

    main(["applied"])

    output = capsys.readouterr().out
    assert "elevenlabs" in output
    assert "status-only" in output
    assert "Submitted remotely." in output


def test_digest_excludes_jobs_applied_in_application_status_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/acme/durable-applied", "Durable Applied PM")
    (tmp_path / "data/application_status.json").write_text(json.dumps({
        "ashby:acme:durable-applied": {"application_status": "applied", "stable_job_key": "ashby:acme:durable-applied"}
    }))

    main(["digest"])

    output = capsys.readouterr().out
    actionable = output.split("Actionable near-fit jobs")[0]
    assert "Durable Applied PM" not in actionable
    assert "applied_count: 1" in output


def test_prep_next_application_excludes_jobs_applied_in_application_status_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/acme/durable-applied-prep", "Applied Prep PM")
    open_id = _insert("https://jobs.ashbyhq.com/acme/durable-open-prep", "Open Prep PM")
    (tmp_path / "data/application_status.json").write_text(json.dumps({
        "ashby:acme:durable-applied-prep": {"application_status": "applied", "stable_job_key": "ashby:acme:durable-applied-prep"}
    }))

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == open_id


def test_unknown_stable_key_status_returns_success_with_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["telegram-command", "applied greenhouse:unknown:12345"])

    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert result["warning"] == "Job was not found in local SQLite, but status was recorded by stable key."
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    assert store["greenhouse:unknown:12345"]["url"] is None


def test_rejected_stable_key_updates_sqlite_status_store_and_history(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/rejected-exists", "Rejected Exists PM")

    main(["telegram-command", "rejected ashby:acme:rejected-exists Rejected after application, no interview"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    record = store["ashby:acme:rejected-exists"]
    assert result["success"] is True
    assert result["new_status"] == "rejected"
    assert result["note"] == "Rejected after application, no interview"
    assert row is not None
    assert row["application_status"] == "rejected"
    assert row["rejected_at"]
    assert row["application_notes"] == "Rejected after application, no interview"
    assert record["application_status"] == "rejected"
    assert record["rejected_at"]
    assert record["note"] == "Rejected after application, no interview"
    assert record["status_history"][-1] == {
        "status": "rejected",
        "timestamp": record["rejected_at"],
        "note": "Rejected after application, no interview",
        "identifier_used": "ashby:acme:rejected-exists",
    }


def test_rejected_stable_key_without_sqlite_job_creates_status_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["telegram-command", "reject ashby:elevenlabs:no-sqlite Not moving forward"])

    result = json.loads(capsys.readouterr().out)
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    record = store["ashby:elevenlabs:no-sqlite"]
    assert result["success"] is True
    assert result["warning"] == "Job was not found in local SQLite, but status was recorded by stable key."
    assert record["application_status"] == "rejected"
    assert record["rejected_at"]
    assert record["company"] == "elevenlabs"
    assert record["note"] == "Not moving forward"
    assert record["status_history"][-1]["identifier_used"] == "ashby:elevenlabs:no-sqlite"


def test_prep_next_application_excludes_rejected_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    rejected_id = _insert("https://jobs.ashbyhq.com/acme/rejected-prep", "Rejected Prep PM")
    open_id = _insert("https://jobs.ashbyhq.com/acme/open-after-rejected", "Open After Rejected PM")
    update_application_tracking(rejected_id, "rejected", rejected_at="2026-05-28T00:00:00+00:00")

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == open_id


def test_rejected_command_lists_rejected_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/list-rejected", "Listed Rejected PM")
    update_application_tracking(job_id, "rejected", rejected_at="2026-05-28T00:00:00+00:00", application_notes="Not selected.")

    main(["rejected"])

    output = capsys.readouterr().out
    assert "Listed Rejected PM" in output
    assert "2026-05-28T00:00:00+00:00" in output
    assert "Not selected." in output


def test_pipeline_command_groups_applied_interviewing_offer(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    applied_id = _insert("https://jobs.ashbyhq.com/acme/pipeline-applied", "Pipeline Applied PM")
    interviewing_id = _insert("https://jobs.ashbyhq.com/acme/pipeline-interviewing", "Pipeline Interviewing PM")
    offer_id = _insert("https://jobs.ashbyhq.com/acme/pipeline-offer", "Pipeline Offer PM")
    update_application_tracking(applied_id, "applied", applied_at="2026-05-28T00:00:00+00:00")
    update_application_tracking(interviewing_id, "interviewing", interviewing_at="2026-05-29T00:00:00+00:00")
    update_application_tracking(offer_id, "offer", offer_at="2026-05-30T00:00:00+00:00")

    main(["pipeline"])

    output = capsys.readouterr().out
    assert "Status: applied" in output
    assert "Pipeline Applied PM" in output
    assert "Status: interviewing" in output
    assert "Pipeline Interviewing PM" in output
    assert "Status: offer" in output
    assert "Pipeline Offer PM" in output


def test_unapplied_high_fit_eligible_only_excludes_ineligible_and_review(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    from job_fit_agent.repository import initialize, upsert_job
    from job_fit_agent.models import JobPosting, FitScore
    initialize()
    rows = [
        ("eligible", "Eligible AI Solutions Engineer"),
        ("ineligible", "DACH Forward Deployed Engineer"),
        ("review", "North America Forward Deployed Engineer"),
    ]
    for idx, (geo, title) in enumerate(rows, start=1):
        job = JobPosting(source="ashby", company="acme", title=title, location="Remote US", url=f"https://jobs.ashbyhq.com/acme/eligible-only-{idx}", description="")
        job.geographic_eligibility = geo
        fit = FitScore(total_score=90, classification="high_fit", role_family="solutions_architecture", viability_level="apply_now")
        upsert_job(job, fit)
    main(["unapplied-high-fit", "--eligible-only"])
    output = capsys.readouterr().out
    assert "Eligible AI Solutions Engineer" in output
    assert "DACH Forward Deployed Engineer" not in output
    assert "North America Forward Deployed Engineer" not in output


def test_blocked_command_with_job_id_updates_status_store_and_history(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/blocked-id", "Blocked ID PM")

    main(["telegram-command", f"blocked {job_id} Ashby 90-day application limit, recruiter/manual review needed"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    store = json.loads((tmp_path / "data/application_status.json").read_text())
    record = store["ashby:acme:blocked-id"]
    assert result["success"] is True
    assert result["new_status"] == "blocked"
    assert result["note"] == "Ashby 90-day application limit, recruiter/manual review needed"
    assert row is not None
    assert row["application_status"] == "blocked"
    assert row["blocked_at"]
    assert row["application_notes"] == "Ashby 90-day application limit, recruiter/manual review needed"
    assert record["application_status"] == "blocked"
    assert record["blocked_at"]
    assert record["note"] == "Ashby 90-day application limit, recruiter/manual review needed"
    assert record["status_history"][-1] == {
        "status": "blocked",
        "timestamp": record["blocked_at"],
        "note": "Ashby 90-day application limit, recruiter/manual review needed",
        "identifier_used": str(job_id),
    }


def test_blocked_command_with_stable_key_updates_sqlite(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/blocked-stable", "Blocked Stable PM")

    main(["telegram-command", "blocked ashby:acme:blocked-stable Ashby limit"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["stable_job_key"] == "ashby:acme:blocked-stable"
    assert row is not None
    assert row["application_status"] == "blocked"
    assert row["blocked_at"]


def test_prep_next_application_excludes_blocked_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    blocked_id = _insert("https://jobs.ashbyhq.com/acme/blocked-prep", "Blocked Prep PM")
    open_id = _insert("https://jobs.ashbyhq.com/acme/open-after-blocked", "Open After Blocked PM")
    update_application_tracking(blocked_id, "blocked", blocked_at="2026-06-04T00:00:00+00:00")

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == open_id


def test_unapplied_high_fit_excludes_blocked_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/blocked-unapplied", "Blocked Unapplied PM")
    update_application_tracking(job_id, "blocked", blocked_at="2026-06-04T00:00:00+00:00")

    main(["unapplied-high-fit"])

    assert "Blocked Unapplied PM" not in capsys.readouterr().out


def test_blocked_report_includes_blocked_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/blocked-report", "Blocked Report PM")
    update_application_tracking(
        job_id,
        "blocked",
        blocked_at="2026-06-04T00:00:00+00:00",
        application_notes="Ashby 90-day application limit, recruiter/manual review needed",
    )

    main(["blocked"])

    output = capsys.readouterr().out
    assert "Blocked, needs relationship strategy" in output
    assert "Blocked Report PM" in output
    assert "Ashby 90-day application limit" in output
    assert "2026-06-04T00:00:00+00:00" in output
    assert "Recruiter/manual review" in output


def test_block_company_creates_durable_company_block(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["block-company", "elevenlabs", "Ashby 90-day application limit, recruiter/manual review only"])

    output = capsys.readouterr().out
    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    record = store["elevenlabs"]
    assert "Blocked company: elevenlabs" in output
    assert record["company"] == "elevenlabs"
    assert record["status"] == "blocked"
    assert record["reason"] == "Ashby 90-day application limit, recruiter/manual review only"
    assert record["blocked_at"]
    assert record["strategy"] == "recruiter/manual review"


def test_prep_next_application_excludes_blocked_company_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    blocked_id = _insert("https://jobs.ashbyhq.com/elevenlabs/company-blocked-prep", "Blocked Company PM")
    open_id = _insert("https://jobs.ashbyhq.com/acme/open-company-blocked-prep", "Open Company PM")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "elevenlabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2026-06-04T00:00:00+00:00",
            "strategy": "recruiter/manual review",
        }
    }))

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == open_id
    assert payload["job_id"] != blocked_id


def test_unapplied_high_fit_excludes_blocked_company_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/elevenlabs/company-blocked-unapplied", "Blocked Company Unapplied PM")
    _insert("https://jobs.ashbyhq.com/acme/open-company-unapplied", "Open Company Unapplied PM")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "ElevenLabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2026-06-04T00:00:00+00:00",
            "strategy": "recruiter/manual review",
        }
    }))

    main(["unapplied-high-fit"])

    output = capsys.readouterr().out
    assert "Blocked Company Unapplied PM" not in output
    assert "Open Company Unapplied PM" in output


def test_force_allows_specific_blocked_company_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/elevenlabs/company-blocked-force", "Blocked Company Force PM")
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "elevenlabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2026-06-04T00:00:00+00:00",
            "strategy": "recruiter/manual review",
        }
    }))

    prep_next_application(dry_run=True, job_id=job_id, force=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == job_id


def test_blocked_report_includes_company_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "elevenlabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2026-06-04T00:00:00+00:00",
            "strategy": "recruiter/manual review",
        }
    }))

    main(["blocked"])

    output = capsys.readouterr().out
    assert "Blocked, needs relationship strategy" in output
    assert "company: elevenlabs" in output
    assert "title: Company-level block" in output
    assert "strategy: recruiter/manual review" in output


def test_block_cli_command_with_job_id_updates_blocked_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/acme/block-cli", "Block CLI PM")

    main(["block", str(job_id), "Ashby", "90-day", "application", "limit"])

    row = get_job_by_id(job_id)
    assert row is not None
    assert row["application_status"] == "blocked"
    assert row["blocked_at"]
    assert row["application_notes"] == "Ashby 90-day application limit"


def test_block_company_with_days_stores_expires_at(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["block-company", "elevenlabs", "Ashby 90-day application limit", "--days", "90"])

    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    record = store["elevenlabs"]
    assert record["status"] == "blocked"
    assert record["expires_at"]
    assert job_main._company_block_days_remaining(record) in {89, 90}


def test_block_company_with_expires_at_stores_exact_date(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["block-company", "elevenlabs", "Ashby 90-day application limit", "--expires-at", "2099-09-02"])

    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    assert store["elevenlabs"]["expires_at"] == "2099-09-02"


def test_expired_company_block_does_not_exclude_prep_next_application(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/elevenlabs/expired-company-block-prep", "Expired Company PM")
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "elevenlabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2000-01-01T00:00:00+00:00",
            "expires_at": "2000-01-02",
            "strategy": "recruiter/manual review",
        }
    }))

    prep_next_application(dry_run=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["job_id"] == job_id


def test_blocked_report_shows_days_remaining_for_company_block(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    main(["block-company", "elevenlabs", "Ashby 90-day application limit", "--days", "90"])
    capsys.readouterr()

    main(["blocked"])

    output = capsys.readouterr().out
    assert "days_remaining:" in output
    assert "expires_at:" in output
    assert "suggested_next_action:" in output


def test_unblock_expired_marks_expired_company_blocks(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data/company_application_blocks.json").write_text(json.dumps({
        "elevenlabs": {
            "company": "elevenlabs",
            "status": "blocked",
            "reason": "Ashby 90-day application limit",
            "blocked_at": "2000-01-01T00:00:00+00:00",
            "expires_at": "2000-01-02",
            "strategy": "recruiter/manual review",
        }
    }))

    main(["unblock-expired"])

    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    assert store["elevenlabs"]["status"] == "expired"
    assert "expired_company_blocks: 1" in capsys.readouterr().out
