import sqlite3

from job_fit_agent.repository import UpsertResult
from job_fit_agent.config import load_target_profile
from job_fit_agent.main import collect_ranked_jobs, collect_scored_jobs, group_jobs_by_classification, main, location_audit
from job_fit_agent.repository import initialize
from job_fit_agent.models import JobPosting


import pytest


@pytest.fixture(autouse=True)
def _stub_repo(monkeypatch):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.upsert_job",
        lambda job, fit: UpsertResult(is_new=True, updated=False, skipped_duplicate=False),
    )



class StubCollector:
    def __init__(self, jobs_by_company, invalid_companies=None):
        self.jobs_by_company = jobs_by_company
        self.invalid_companies = set(invalid_companies or [])

    def validate_company_token(self, company: str) -> bool:
        return company not in self.invalid_companies

    def fetch_jobs(self, company: str):
        return self.jobs_by_company.get(company, [])


def _job(title: str, location: str = "Remote", description: str = "AI analytics") -> JobPosting:
    return JobPosting(
        source="greenhouse",
        company="openai",
        title=title,
        location=location,
        url=f"https://example.com/{title.lower().replace(' ', '-')}",
        description=description,
    )


def test_collect_ranked_jobs_sorts_high_to_low() -> None:
    high = _job("Product Manager AI")
    mid = _job("Product Owner")
    low = _job("Software Engineer", description="backend systems")

    collector = StubCollector({"openai": [mid, low, high]})

    ranked = collect_ranked_jobs(collector=collector, target_profile=load_target_profile(), companies=["openai"], min_score=0)

    scores = [fit.total_score for _, fit in ranked]
    assert scores == sorted(scores, reverse=True)


def test_collect_ranked_jobs_excludes_below_threshold() -> None:
    keep = _job("Product Manager AI", location="Remote US")
    drop = _job("Software Engineer", location="On-site", description="backend systems")

    collector = StubCollector({"openai": [keep, drop]})

    ranked = collect_ranked_jobs(collector=collector, target_profile=load_target_profile(), companies=["openai"], min_score=45)

    assert len(ranked) == 1
    assert ranked[0][0].title == "Product Manager AI"


def test_collect_ranked_jobs_includes_reasons_in_fit_score() -> None:
    job = _job("Product Manager AI", location="Remote", description="AI and data roadmap")
    collector = StubCollector({"openai": [job]})

    ranked = collect_ranked_jobs(collector=collector, target_profile=load_target_profile(), companies=["openai"], min_score=0)

    assert ranked
    _, fit = ranked[0]
    assert fit.reasons


def test_collect_scored_jobs_returns_sorted_below_threshold_for_debugging() -> None:
    keep = _job("Product Manager AI", location="Remote US")
    almost = _job("Product Manager", location="Pittsburgh", description="analytics")
    low = _job("Software Engineer", location="On-site", description="backend systems")

    collector = StubCollector({"openai": [low, keep, almost]})

    ranked, below = collect_scored_jobs(
        collector=collector,
        target_profile=load_target_profile(),
        companies=["openai"],
        min_score=45,
    )

    assert len(ranked) == 1
    assert ranked[0][0].title == "Product Manager AI"
    assert len(below) == 2
    assert below[0][1].total_score >= below[1][1].total_score


def test_main_combined_greenhouse_ashby_pipeline_without_lever(monkeypatch, capsys) -> None:
    gh_job = _job("Product Manager AI", location="Remote US")
    ashby_job = JobPosting(source="ashby", company="anthropic", title="Technical Program Manager", location="Remote US", url="https://example.com/a", description="program delivery")
    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [gh_job]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({"anthropic": [ashby_job]}))
    monkeypatch.setattr(
        "job_fit_agent.main.resolve_companies",
        lambda source="greenhouse": ["openai"] if source == "greenhouse" else ["anthropic"],
    )

    main(["run"])
    output = capsys.readouterr().out

    assert "High-fit jobs to review" in output
    assert "title: Product Manager AI" in output
    assert "title: Technical Program Manager" in output


def test_main_includes_lever_when_enabled(monkeypatch, capsys) -> None:
    gh_job = _job("Product Manager AI", location="Remote US")
    ashby_job = JobPosting(source="ashby", company="anthropic", title="Technical Program Manager", location="Remote US", url="https://example.com/a", description="program delivery")
    lever_job = JobPosting(source="lever", company="ramp", title="Senior Product Manager", location="Remote US", url="https://example.com/l", description="payments roadmap")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [gh_job]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({"anthropic": [ashby_job]}))
    monkeypatch.setattr("job_fit_agent.main.LeverCollector", lambda: StubCollector({"ramp": [lever_job]}))
    monkeypatch.setattr("job_fit_agent.main.AppConfig", lambda: type("Cfg", (), {"enable_lever": True})())
    monkeypatch.setattr(
        "job_fit_agent.main.resolve_companies",
        lambda source="greenhouse": ["openai"] if source == "greenhouse" else (["anthropic"] if source == "ashby" else ["ramp"]),
    )

    main(["run"])
    output = capsys.readouterr().out

    assert "High-fit jobs to review" in output
    assert "title: Product Manager AI" in output
    assert "No new matching jobs found." not in output


def test_group_jobs_by_classification_groups_buckets() -> None:
    high = _job("Product Manager AI", location="Remote US")
    near = _job("Technical Program Manager", location="Remote US", description="program delivery")
    low = _job("Software Engineer", location="On-site", description="backend systems")

    ranked, _ = collect_scored_jobs(
        collector=StubCollector({"openai": [high, near, low]}),
        target_profile=load_target_profile(),
        companies=["openai"],
        min_score=0,
    )
    high_jobs, near_jobs, low_jobs = group_jobs_by_classification(ranked)

    assert len(high_jobs) >= 1
    assert len(near_jobs) == 1
    assert len(low_jobs) == 1


def test_main_prints_near_fit_section_and_hides_low_fit_when_near_fit_exists(monkeypatch, capsys) -> None:
    near = _job("Technical Program Manager", location="Remote US", description="program delivery")
    low = _job("Software Engineer", location="On-site", description="backend systems")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [near, low]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    main(["run"])
    output = capsys.readouterr().out

    assert "No new matching jobs found." not in output
    assert "Near-fit jobs worth reviewing" in output


def test_main_prints_low_fit_debug_only_when_no_high_or_near(monkeypatch, capsys) -> None:
    low = _job("Software Engineer", location="On-site", description="backend systems")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [low]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    main(["run"])
    output = capsys.readouterr().out

    assert "No new matching jobs found." in output
    assert "Near-fit jobs worth reviewing" not in output
    assert "Top low-fit jobs for review" not in output


def test_main_prints_high_fit_jobs_when_present(monkeypatch, capsys) -> None:
    high = _job("Product Manager AI", location="Remote US")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [high]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    main(["run"])
    output = capsys.readouterr().out

    assert "High-fit jobs to review" in output
    assert "score:" in output
    assert "classification: high_fit" in output
    assert "source: greenhouse" in output
    assert "title: Product Manager AI" in output
    assert "company: openai" in output
    assert "location: Remote US" in output
    assert "workplace_type:" in output
    assert "department:" in output
    assert "team:" in output
    assert "url:" in output
    assert "reasons:" in output
    assert "red_flags:" in output


def test_main_prints_near_fit_after_high_fit(monkeypatch, capsys) -> None:
    high = _job("Product Manager AI", location="Remote US")
    near = _job("Technical Program Manager", location="Remote US", description="program delivery")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [high, near]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    main(["run"])
    output = capsys.readouterr().out

    assert output.index("High-fit jobs to review") < output.index("Near-fit jobs worth reviewing")


def test_main_does_not_print_no_high_fit_message_when_high_fit_exists(monkeypatch, capsys) -> None:
    high = _job("Product Manager AI", location="Remote US")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [high]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    main(["run"])
    output = capsys.readouterr().out

    assert "No new matching jobs found." not in output


def test_collect_scored_jobs_aggregates_successful_companies_only() -> None:
    keep = _job("Product Manager AI", location="Remote US")
    dropped = _job("Software Engineer", location="On-site", description="backend")
    collector = StubCollector({"openai": [keep], "badtoken": [dropped]}, invalid_companies={"badtoken"})

    ranked, below = collect_scored_jobs(
        collector=collector,
        target_profile=load_target_profile(),
        companies=["openai", "badtoken"],
        min_score=0,
    )

    titles = [job.title for job, _ in ranked + below]
    assert "Product Manager AI" in titles
    assert "Software Engineer" not in titles


def test_digest_does_not_call_collectors(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [],
    )
    monkeypatch.setattr(
        "job_fit_agent.main.GreenhouseCollector",
        lambda: (_ for _ in ()).throw(AssertionError("collector should not be constructed for digest")),
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Saved high-fit jobs" in output
    assert "No saved high-fit jobs." in output
    assert "Saved near-fit jobs" in output
    assert "No saved near-fit jobs." in output


def test_digest_returns_saved_high_fit_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "score": 97,
                "id": 1,
                "status": "new",
                "title": "Staff PM",
                "company": "openai",
                "source": "greenhouse",
                "url": "https://example.com/high_fit",
                "red_flags": "[]",
            }
        ]
        if classification == "high_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Saved high-fit jobs" in output
    assert "id: 1" in output
    assert "score: 97" in output
    assert "status: new" in output
    assert "title: Staff PM" in output


def test_digest_returns_saved_near_fit_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "score": 74,
                "id": 2,
                "status": "interested",
                "title": "TPM",
                "company": "openai",
                "source": "greenhouse",
                "url": "https://example.com/near_fit",
                "red_flags": "[]",
            }
        ]
        if classification == "near_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Saved near-fit jobs" in output
    assert "id: 2" in output
    assert "score: 74" in output
    assert "status: interested" in output
    assert "title: TPM" in output


def test_mark_command_updates_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: {"status": "applied"})
    main(["mark", "5", "applied"])
    output = capsys.readouterr().out
    assert "Updated job 5 status to applied." in output


def test_notes_command_updates_notes(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.update_notes", lambda job_id, notes: None)
    main(["notes", "5", "follow up next week"])
    output = capsys.readouterr().out
    assert "Updated job 5 notes." in output


def test_digest_prints_both_sections_with_empty_messages(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Saved high-fit jobs" in output
    assert "No saved high-fit jobs." in output
    assert "Saved near-fit jobs" in output
    assert "No saved near-fit jobs." in output


def test_digest_uses_saved_jobs_not_only_new_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)

    def _top_jobs(classification, limit=10):
        if classification == "high_fit":
                return [{
                "id": 3,
                    "score": 88,
                    "status": "new",
                    "title": "Saved Existing High",
                "company": "openai",
                "source": "greenhouse",
                "url": "https://example.com/saved_high",
                "red_flags": "[]",
                "first_seen_at": "2026-05-01T00:00:00+00:00",
                "last_seen_at": "2026-05-05T00:00:00+00:00",
            }]
        return []

    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", _top_jobs)
    monkeypatch.setattr(
        "job_fit_agent.main.GreenhouseCollector",
        lambda: (_ for _ in ()).throw(AssertionError("collector should not be constructed for digest")),
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Saved Existing High" in output
    assert "No saved high-fit jobs." not in output


def test_digest_command_does_not_call_run_pipeline(monkeypatch) -> None:
    called = {"run": False}
    monkeypatch.setattr("job_fit_agent.main.print_digest", lambda group_by_status=False: None)

    def _fail_run() -> None:
        called["run"] = True

    monkeypatch.setattr("job_fit_agent.main.run_pipeline", _fail_run)

    main(["digest"])

    assert called["run"] is False


def test_digest_command_does_not_print_no_new_matching_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", lambda classification, limit=10: [])

    main(["digest"])
    output = capsys.readouterr().out

    assert "No new matching jobs found." not in output


def test_digest_prints_sqlite_row_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE jobs (id INTEGER, score INTEGER, status TEXT, title TEXT, company TEXT, source TEXT, url TEXT, red_flags TEXT, classification TEXT, viability_level TEXT, viability_reasons TEXT)"
    )
    conn.execute(
        "INSERT INTO jobs VALUES (1, 97, 'new', 'Staff PM', 'openai', 'greenhouse', 'https://example.com/high_fit', '[]', 'high_fit', 'review', '[\"reason\"]')"
    )
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row is not None

    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [row] if classification == "high_fit" else [],
    )

    main(["digest"])
    output = capsys.readouterr().out
    assert "id: 1" in output
    assert "classification: high_fit" in output
    assert "viability_level: review" in output
    assert "viability_reasons: reason" in output


def test_safe_row_value_returns_default_for_missing_optional_field() -> None:
    from job_fit_agent.main import safe_row_value

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE jobs (id INTEGER)")
    conn.execute("INSERT INTO jobs VALUES (1)")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row is not None

    assert safe_row_value(row, "classification", "unknown") == "unknown"


def test_digest_does_not_crash_when_viability_fields_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "score": 88,
                "id": 3,
                "status": "new",
                "title": "Saved Existing High",
                "company": "openai",
                "source": "greenhouse",
                "url": "https://example.com/saved_high",
                "red_flags": "[]",
            }
        ]
        if classification == "high_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out
    assert "viability_level: review" in output
    assert "viability_reasons: none" in output


def test_run_command_calls_run_pipeline(monkeypatch) -> None:
    called = {"run": False}

    def _run() -> None:
        called["run"] = True

    monkeypatch.setattr("job_fit_agent.main.run_pipeline", _run)

    main(["run"])

    assert called["run"] is True


def test_rescore_updates_existing_jobs_without_notifications(monkeypatch, capsys) -> None:
    high = _job("Product Manager AI", location="Remote US")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [high]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({}))
    monkeypatch.setattr("job_fit_agent.main.LeverCollector", lambda: StubCollector({}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    called = {"sent": False}

    def _fail_send(_message: str) -> None:
        called["sent"] = True

    monkeypatch.setattr("job_fit_agent.main.send_message", _fail_send)
    monkeypatch.setattr(
        "job_fit_agent.main.upsert_job",
        lambda job, fit: UpsertResult(is_new=False, updated=True, skipped_duplicate=False),
    )

    main(["rescore"])
    output = capsys.readouterr().out

    assert "Rescore complete" in output
    assert "updated jobs count: 1" in output
    assert called["sent"] is False


def test_set_status_command_updates_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: {"status": "interested"})
    main(["set-status", "8", "interested"])
    output = capsys.readouterr().out
    assert "Updated job 8 status to interested." in output


def test_list_status_command_prints_results(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.get_jobs_by_status", lambda status: [{"id": 8, "score": 80, "status": status, "classification": "near_fit", "title": "PM", "company": "openai", "source": "greenhouse", "url": "https://example.com/8", "viability_reasons": "[]", "red_flags": "[]"}])
    main(["list-status", "interested"])
    output = capsys.readouterr().out
    assert "Jobs with status 'interested'" in output
    assert "id: 8" in output


def test_digest_grouped_by_status(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", lambda classification, limit=10: [{"id": 1, "score": 97, "status": "interested", "classification": classification, "title": "Staff PM", "company": "openai", "source": "greenhouse", "url": "https://example.com/1", "viability_reasons": "[]", "red_flags": "[]"}] if classification == "high_fit" else [])
    main(["digest", "--group-by-status"])
    output = capsys.readouterr().out
    assert "Saved jobs grouped by status" in output
    assert "Status: interested" in output


def test_location_audit_reports_blank_and_region_only(tmp_path, monkeypatch, capsys) -> None:
    db_path = tmp_path / "jobs.sqlite"
    monkeypatch.setattr("job_fit_agent.main.DB_PATH", db_path)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: initialize(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE jobs (
            source TEXT, company TEXT, url TEXT, location_raw TEXT, normalized_location_type TEXT,
            geographic_eligibility TEXT, workplace_type TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ashby", "elevenlabs", "https://jobs.ashbyhq.com/elevenlabs/a", "", "unknown", "review", ""),
        )
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ashby", "cursor", "https://jobs.ashbyhq.com/cursor/d", "", "unknown", "review", ""),
        )
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ashby", "linear", "https://jobs.ashbyhq.com/linear/b", "Europe", "remote", "ineligible", "Remote"),
        )
        conn.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ashby", "linear", "https://jobs.ashbyhq.com/linear/c", "North America", "remote", "review", "Remote"),
        )
    location_audit()
    output = capsys.readouterr().out
    assert "A. Blank location_raw needing debugging" in output
    assert "ashby/elevenlabs: 1" in output
    assert "ashby/cursor: 1" not in output.split("A. Blank location_raw needing debugging")[1].split("B. Region-only locations by company")[0]
    assert "B. Region-only locations by company" in output
    assert "ashby/linear: 2" in output
    assert "E. Known source limitations" in output
    assert "ashby/cursor: 1" in output


def test_location_audit_command_runs(monkeypatch) -> None:
    called = {"ok": False}
    monkeypatch.setattr("job_fit_agent.main.location_audit", lambda: called.__setitem__("ok", True))
    main(["location-audit"])
    assert called["ok"] is True

def test_prep_application_creates_package_and_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job = {
        "id": 8,
        "title": "Product Manager Builder",
        "company": "Perplexity",
        "source": "ashby",
        "url": "https://example.com/job/8",
        "score": 88,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": '["seniority stretch"]',
        "viability_reasons": '["remote_eligible"]',
        "status": "new",
        "notes": "",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text("- Perplexity — PM — 2024-Present\n", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job if job_id == 8 else None)
    updated = []
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: updated.append((job_id, status)))

    main(["prep-application", "8"])

    app_dir = tmp_path / "applications" / "perplexity_product_manager_builder_8"
    assert app_dir.exists()
    expected_files = {
        "fit_summary.md",
        "resume_strategy.md",
        "tailored_resume_draft.md",
        "recruiter_note.md",
        "application_questions.md",
        "risk_flags.md",
    }
    assert expected_files.issubset({p.name for p in app_dir.iterdir()})
    fit_text = (app_dir / "fit_summary.md").read_text(encoding="utf-8")
    assert "Product Manager Builder" in fit_text
    assert "Perplexity" in fit_text
    assert "https://example.com/job/8" in fit_text
    risk_text = (app_dir / "risk_flags.md").read_text(encoding="utf-8").lower()
    assert "location risk" in risk_text
    assert "seniority risk" in risk_text
    assert updated == [(8, "interested")]


def test_prep_application_missing_job_prints_clear_error(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: None)

    main(["prep-application", "999"])
    output = capsys.readouterr().out
    assert "Job not found: 999" in output


def test_prep_application_missing_base_resume_prints_clear_error(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    job = {
        "id": 8,
        "title": "Product Manager Builder",
        "company": "Perplexity",
        "source": "ashby",
        "url": "https://example.com/job/8",
        "score": 88,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": '["seniority stretch"]',
        "viability_reasons": '["remote_eligible"]',
        "status": "new",
        "notes": "",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)

    main(["prep-application", "8"])

    output = capsys.readouterr().out
    assert "Missing profile/base_resume.md. Add your base resume before running prep-application." in output


def test_prep_application_tailored_resume_uses_base_resume_and_preserves_entities(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile_context.yaml").write_text("strengths:\n  - workflow automation\n", encoding="utf-8")
    (profile_dir / "resume_rules.yaml").write_text("rules:\n  - Preserve company names\n  - Preserve dates\n", encoding="utf-8")
    (profile_dir / "base_resume.md").write_text(
        "- Perplexity — Product Manager — 2024-Present\n- Walmart — Product Ops — 2021-2023\n",
        encoding="utf-8",
    )
    job = {
        "id": 8,
        "title": "Product Manager Builder",
        "company": "Perplexity",
        "source": "ashby",
        "url": "https://example.com/job/8",
        "score": 88,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": '["seniority stretch"]',
        "viability_reasons": '["remote_eligible"]',
        "status": "new",
        "notes": "",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job if job_id == 8 else None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "8"])

    resume_text = (tmp_path / "applications" / "perplexity_product_manager_builder_8" / "tailored_resume_draft.md").read_text(encoding="utf-8")
    assert "Perplexity — Product Manager — 2024-Present" in resume_text
    assert "Walmart — Product Ops — 2021-2023" in resume_text
    assert "Placeholder Company" not in resume_text
    assert "## Positioning" in resume_text
    assert "AI-native product builder/operator" in resume_text
    assert "[insert metric if available]" in resume_text



def test_prep_application_changes_headline_and_prioritizes_ai_projects(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile_context.yaml").write_text("strengths:\n  - AI agents\n", encoding="utf-8")
    (profile_dir / "resume_rules.yaml").write_text("rules:\n  - Preserve company names\n", encoding="utf-8")
    (profile_dir / "base_resume.md").write_text("- Resorts World — Product Systems — 2022-Present\n", encoding="utf-8")
    job = {
        "id": 20,
        "title": "AI Product Manager",
        "company": "Acme AI",
        "source": "ashby",
        "url": "https://example.com/job/20",
        "score": 92,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": "[]",
        "viability_reasons": '["ai_role"]',
        "role_family": "ai_product",
        "status": "new",
        "notes": "Agentic workflow automation role.",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "20"])

    app_dir = tmp_path / "applications" / "acme_ai_ai_product_manager_20"
    strategy_text = (app_dir / "resume_strategy.md").read_text(encoding="utf-8")
    resume_text = (app_dir / "tailored_resume_draft.md").read_text(encoding="utf-8")
    assert "## Recommended headline" in strategy_text
    assert "Job Fit Agent" in strategy_text
    assert "RWLV Priority Governor Agent" in strategy_text
    assert "Job Fit Agent" in resume_text
    assert "RWLV Priority Governor Agent" in resume_text


def test_prep_application_creates_all_expected_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text("- Acme — PM — 2020-2024\n", encoding="utf-8")
    job = {
        "id": 30,
        "title": "Product Manager",
        "company": "Beta",
        "source": "lever",
        "url": "https://example.com/job/30",
        "score": 80,
        "classification": "near_fit",
        "viability_level": "review",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": "[]",
        "red_flags": "[]",
        "viability_reasons": "[]",
        "status": "new",
        "notes": "",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "30"])
    app_dir = tmp_path / "applications" / "beta_product_manager_30"
    for name in ["fit_summary.md", "resume_strategy.md", "tailored_resume_draft.md", "recruiter_note.md", "application_questions.md", "risk_flags.md"]:
        assert (app_dir / name).exists()


def test_prep_application_does_not_overwrite_applied_status(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job = {
        "id": 12,
        "title": "Senior Product Manager",
        "company": "Acme",
        "source": "greenhouse",
        "url": "https://example.com/job/12",
        "score": 70,
        "classification": "near_fit",
        "viability_level": "review",
        "location_raw": "Las Vegas, NV",
        "location": "Las Vegas, NV",
        "geographic_eligibility": "review",
        "reasons": "[]",
        "red_flags": "[]",
        "viability_reasons": "[]",
        "status": "applied",
        "notes": "",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    called = []
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: called.append((job_id, status)))

    main(["prep-application", "12"])

    assert called == []


def _build_sqlite_job_row(**overrides):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE jobs (
            id INTEGER, title TEXT, company TEXT, source TEXT, url TEXT, score INTEGER,
            classification TEXT, viability_level TEXT, location_raw TEXT, location TEXT,
            geographic_eligibility TEXT, reasons TEXT, red_flags TEXT, viability_reasons TEXT,
            status TEXT, notes TEXT, role_family TEXT
        )"""
    )
    base = {
        "id": 15,
        "title": "Product Manager Builder",
        "company": "Perplexity",
        "source": "ashby",
        "url": "https://example.com/job/15",
        "score": 88,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": "[]",
        "viability_reasons": "[]",
        "status": "new",
        "notes": "",
        "role_family": "ai_product",
    }
    base.update(overrides)
    conn.execute(
        "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(base[key] for key in [
            "id", "title", "company", "source", "url", "score", "classification", "viability_level",
            "location_raw", "location", "geographic_eligibility", "reasons", "red_flags",
            "viability_reasons", "status", "notes", "role_family"
        ]),
    )
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row is not None
    return conn, row


def test_prep_application_accepts_sqlite_row(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text("- Perplexity — PM — 2024-Present\n", encoding="utf-8")
    conn, row = _build_sqlite_job_row()
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: row if job_id == 15 else None)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    try:
        main(["prep-application", "15"])
    finally:
        conn.close()

    app_dir = tmp_path / "applications" / "perplexity_product_manager_builder_15"
    assert app_dir.exists()


def test_prep_application_sqlite_row_missing_optional_fields_uses_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text("- Perplexity — PM — 2024-Present\n", encoding="utf-8")
    conn, row = _build_sqlite_job_row(location_raw=None, notes=None, role_family=None)
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: row)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    try:
        main(["prep-application", "15"])
    finally:
        conn.close()

    strategy_text = (tmp_path / "applications" / "perplexity_product_manager_builder_15" / "resume_strategy.md").read_text(encoding="utf-8")
    assert "the role family implied by the JD" in strategy_text
