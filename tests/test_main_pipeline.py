import sqlite3
import re
import sys
import types
from pathlib import Path
import yaml

from job_fit_agent.repository import UpsertResult
from job_fit_agent.config import AppConfig, load_target_profile
from job_fit_agent.main import _is_actionable_real_job_url, _normalize_submit_resume, _select_projects_for_role, collect_ranked_jobs, collect_scored_jobs, group_jobs_by_classification, main, location_audit
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



def test_app_config_enable_lever_defaults_false(monkeypatch) -> None:
    monkeypatch.delenv("JOB_FIT_ENABLE_LEVER", raising=False)

    assert AppConfig().enable_lever is False


def test_app_config_enable_lever_accepts_true(monkeypatch) -> None:
    monkeypatch.setenv("JOB_FIT_ENABLE_LEVER", "true")

    assert AppConfig().enable_lever is True


def test_app_config_enable_lever_accepts_one(monkeypatch) -> None:
    monkeypatch.setenv("JOB_FIT_ENABLE_LEVER", "1")

    assert AppConfig().enable_lever is True


def test_actionable_real_job_url_allows_greenhouse_ashby_and_lever() -> None:
    assert _is_actionable_real_job_url("https://job-boards.greenhouse.io/openai/jobs/123")
    assert _is_actionable_real_job_url("https://jobs.ashbyhq.com/replit/abc")
    assert _is_actionable_real_job_url("https://jobs.lever.co/ramp/def")




def test_actionable_real_job_url_rejects_example_and_localhost() -> None:
    assert _is_actionable_real_job_url("https://example.com/product-manager-ai") is False
    assert _is_actionable_real_job_url("https://localhost:3000/job/1") is False

def test_digest_excludes_placeholder_urls_from_default_sections(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=50: [
            {
                "id": 1,
                "score": 95,
                "status": "new",
                "classification": classification,
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
                "title": "Product Manager",
                "company": "x",
                "source": "greenhouse",
                "url": "https://example.com/product-manager-ai",
                "viability_reasons": "[]",
                "red_flags": "[]",
            }
        ] if classification == "high_fit" else [],
    )
    main(["digest"])
    output = capsys.readouterr().out
    assert "https://example.com/product-manager-ai" not in output

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


def test_run_skips_lever_when_disabled(monkeypatch, capsys) -> None:
    gh_job = _job("Product Manager AI", location="Remote US")
    ashby_job = JobPosting(source="ashby", company="anthropic", title="Technical Program Manager", location="Remote US", url="https://example.com/a", description="program delivery")

    monkeypatch.delenv("JOB_FIT_ENABLE_LEVER", raising=False)
    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [gh_job]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({"anthropic": [ashby_job]}))
    monkeypatch.setattr(
        "job_fit_agent.main.LeverCollector",
        lambda: (_ for _ in ()).throw(AssertionError("Lever collector should not be constructed when disabled")),
    )
    monkeypatch.setattr(
        "job_fit_agent.main.resolve_companies",
        lambda source="greenhouse": ["openai"] if source == "greenhouse" else (["anthropic"] if source == "ashby" else ["ramp"]),
    )

    main(["run"])
    output = capsys.readouterr().out

    assert "lever enabled: False" in output
    assert "title: Product Manager AI" in output
    assert "title: Technical Program Manager" in output


def test_run_includes_lever_when_enabled(monkeypatch, capsys) -> None:
    gh_job = _job("Product Manager AI", location="Remote US")
    ashby_job = JobPosting(source="ashby", company="anthropic", title="Technical Program Manager", location="Remote US", url="https://example.com/a", description="program delivery")
    lever_job = JobPosting(source="lever", company="ramp", title="Senior Product Manager", location="Remote US", url="https://example.com/l", description="payments roadmap")

    monkeypatch.setenv("JOB_FIT_ENABLE_LEVER", "true")
    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [gh_job]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({"anthropic": [ashby_job]}))
    monkeypatch.setattr("job_fit_agent.main.LeverCollector", lambda: StubCollector({"ramp": [lever_job]}))
    monkeypatch.setattr(
        "job_fit_agent.main.resolve_companies",
        lambda source="greenhouse": ["openai"] if source == "greenhouse" else (["anthropic"] if source == "ashby" else ["ramp"]),
    )

    main(["run"])
    output = capsys.readouterr().out

    assert "lever enabled: True" in output
    assert "High-fit jobs to review" in output
    assert "title: Product Manager AI" in output
    assert "title: Senior Product Manager" in output
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

    assert "Actionable high-fit jobs" in output
    assert "No actionable high-fit jobs." in output
    assert "Actionable near-fit jobs" in output
    assert "No actionable near-fit jobs." in output


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
                "url": "https://jobs.lever.co/acme/high_fit",
                "red_flags": "[]",
            }
        ]
        if classification == "high_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Actionable high-fit jobs" in output
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
                "url": "https://jobs.lever.co/acme/near_fit",
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
                "red_flags": "[]",
            }
        ]
        if classification == "near_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Actionable near-fit jobs" in output
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

    assert "Actionable high-fit jobs" in output
    assert "No actionable high-fit jobs." in output
    assert "Actionable near-fit jobs" in output
    assert "No actionable near-fit jobs." in output


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
                "url": "https://jobs.lever.co/acme/saved_high",
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
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
    assert "No actionable high-fit jobs." not in output


def test_digest_command_does_not_call_run_pipeline(monkeypatch) -> None:
    called = {"run": False}
    monkeypatch.setattr("job_fit_agent.main.print_digest", lambda group_by_status=False, include_skipped=False: None)

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
        "INSERT INTO jobs VALUES (1, 97, 'new', 'Staff PM', 'openai', 'greenhouse', 'https://jobs.lever.co/acme/high_fit', '[]', 'high_fit', 'review', '[\"reason\"]')"
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
                "url": "https://jobs.lever.co/acme/saved_high",
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
        "resume_draft.md",
        "recruiter_note.md",
        "answer_bank.md",
        "risk_flags.md",
        "cover_letter.md",
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

    resume_text = (tmp_path / "applications" / "perplexity_product_manager_builder_8" / "resume_draft.md").read_text(encoding="utf-8")
    assert "Perplexity — Product Manager — 2024-Present" in resume_text
    assert "Walmart — Product Ops — 2021-2023" in resume_text
    assert "Placeholder Company" not in resume_text
    assert "## Positioning" in resume_text
    assert "Technical product builder focused on AI-enabled workflow systems" in resume_text
    assert "AI-native" not in resume_text
    assert "[insert metric if available]" not in resume_text



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
    resume_text = (app_dir / "resume_draft.md").read_text(encoding="utf-8")
    assert "## Recommended headline" in strategy_text
    assert "Job Fit Agent" in strategy_text
    assert "RWLV Priority Governor Agent" in strategy_text
    assert "Job Fit Agent" in resume_text
    assert "RWLV Priority Governor Agent" in resume_text



def _prep_job(title: str, company: str = "ElevenLabs", role_family: str = "", notes: str = "") -> dict:
    return {
        "id": 88,
        "title": title,
        "company": company,
        "source": "ashby",
        "url": "https://example.com/job/88",
        "score": 92,
        "classification": "high_fit",
        "viability_level": "apply_now",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": '["Strong alignment"]',
        "red_flags": "[]",
        "viability_reasons": '["role aligned"]',
        "role_family": role_family,
        "status": "new",
        "notes": notes,
    }


def test_role_family_project_ordering_rules():
    assert _select_projects_for_role(
        "Enterprise Solutions Engineer - North America",
        "solutions_engineering",
        "customer-facing AI workflow implementation and internal tools",
    )[:3] == ["AI Product Design Operating System", "Job Fit Agent", "RWLV Priority Governor Agent"]
    assert _select_projects_for_role(
        "AI Enablement Manager",
        "ai_operations",
        "marketing workflow automation, reporting analytics, and implementation",
    )[:3] == ["Marketing Intelligence OS", "AI Product Design Operating System", "Job Fit Agent"]
    assert _select_projects_for_role("Forward Deployed Engineer", "", "AI implementation")[:3] == [
        "Marketing Intelligence OS",
        "AI Product Design Operating System",
        "Job Fit Agent",
    ]
    assert _select_projects_for_role("AI Transformation Lead", "", "workflow automation")[:3] == [
        "Marketing Intelligence OS",
        "AI Product Design Operating System",
        "Job Fit Agent",
    ]
    assert _select_projects_for_role("Product Manager", "", "roadmap and agentic workflows")[:3] == [
        "AI Product Design Operating System",
        "RWLV Priority Governor Agent",
        "Job Fit Agent",
    ]
    assert _select_projects_for_role("Product Systems Analytics Manager", "", "instrumentation and product systems")[:3] == [
        "RWLV Priority Governor Agent",
        "AI Product Design Operating System",
        "Job Fit Agent",
    ]


def test_enterprise_solutions_engineer_resume_includes_ai_product_design_and_avoids_ai_native(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile_context.yaml").write_text("strengths:\n  - workflow automation\n  - product analytics\n", encoding="utf-8")
    (profile_dir / "resume_rules.yaml").write_text("rules:\n  - Preserve company names\n", encoding="utf-8")
    (profile_dir / "base_resume.md").write_text("# Cody McKeon\n\n## Professional Summary\nBuilder summary.\n", encoding="utf-8")
    job = _prep_job(
        "Enterprise Solutions Engineer - North America",
        role_family="solutions_engineering",
        notes="customer-facing technical discovery, AI workflow implementation, APIs, internal tools, and product analytics",
    )
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "88"])

    app_dir = tmp_path / "applications" / "elevenlabs_enterprise_solutions_engineer_north_america_88"
    resume_text = (app_dir / "resume_draft.md").read_text(encoding="utf-8")
    strategy_text = (app_dir / "resume_strategy.md").read_text(encoding="utf-8")
    cover_text = (app_dir / "cover_letter.md").read_text(encoding="utf-8")
    recruiter_text = (app_dir / "recruiter_note.md").read_text(encoding="utf-8")
    assert "AI Product Design Operating System" in resume_text
    assert "Current State" in resume_text
    assert "Job Fit Agent" in resume_text
    assert "RWLV Priority Governor Agent" in resume_text
    assert "AI-native" not in resume_text
    assert "Technical product and AI workflow builder" in resume_text
    assert "Technical Product Builder | AI Workflow Systems | Product Analytics | Solutions Engineering" in strategy_text
    assert "customer-facing technical problem solving" in strategy_text
    assert "AI Product Design Operating System" in cover_text
    assert "GitHub Actions" in cover_text
    assert "AI Product Design Operating System" in recruiter_text


def test_generated_summary_avoids_buzzword_heavy_phrasing_for_product_manager(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text("- Acme — PM — 2020-2024\n", encoding="utf-8")
    job = _prep_job("Product Manager", company="Acme", notes="automation and agentic workflows")
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "88"])

    resume_text = (tmp_path / "applications" / "acme_product_manager_88" / "resume_draft.md").read_text(encoding="utf-8")
    assert "AI Product Design Operating System" in resume_text
    assert "Job Fit Agent" in resume_text
    assert "RWLV Priority Governor Agent" in resume_text
    assert "Technical product builder focused on AI-enabled workflow systems" in resume_text
    assert "AI-native" not in resume_text

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
    for name in ["fit_summary.md", "resume_strategy.md", "resume_draft.md", "submit_resume.md", "recruiter_note.md", "answer_bank.md", "risk_flags.md", "cover_letter.md"]:
        assert (app_dir / name).exists()


def test_cover_letter_content_and_style(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "profile_context.yaml").write_text("strengths:\n  - workflow automation\n", encoding="utf-8")
    (profile_dir / "resume_rules.yaml").write_text("rules:\n  - Keep claims verifiable\n", encoding="utf-8")
    (profile_dir / "base_resume.md").write_text("- Acme — PM — 2020-2024\n", encoding="utf-8")
    job = {
        "id": 35,
        "title": "Senior Product Manager, AI Workflows",
        "company": "linear",
        "source": "lever",
        "url": "https://example.com/job/35",
        "score": 85,
        "classification": "near_fit",
        "viability_level": "review",
        "location_raw": "Remote US",
        "location": "Remote US",
        "geographic_eligibility": "eligible",
        "reasons": "[]",
        "red_flags": "[]",
        "viability_reasons": "[]",
        "status": "new",
        "notes": "Lead product systems and AI-assisted workflow automation.",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "35"])
    text = (tmp_path / "applications" / "linear_senior_product_manager_ai_workflows_35" / "cover_letter.md").read_text(encoding="utf-8")

    assert "Cody McKeon" in text
    assert "Linear" in text
    assert "Dear Linear Hiring Team," in text
    assert "Senior Product Manager, AI Workflows" in text
    assert "Based on the role description," in text
    assert "http://" not in text
    assert "https://" not in text
    assert "Resume Rules Applied" not in text
    assert "Tailored Resume Draft" not in text
    assert "Positioning" not in text
    assert "[insert metric if available]" not in text
    assert "—" not in text


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


def test_export_resume_pdf_uses_submit_resume_and_output_naming(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "applications" / "acme_ai_product_manager_core_api_5"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "submit_resume.md").write_text("# Resume", encoding="utf-8")
    job = {"id": 5, "company": "Acme / AI", "title": "Product Manager (Core/API)"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    called = {}

    def fake_run(cmd, check):
        called["cmd"] = cmd
        called["check"] = check

    monkeypatch.setattr("job_fit_agent.main.subprocess.run", fake_run)
    main(["export-resume-pdf", "5"])
    assert called["check"] is True
    assert called["cmd"][0] == "pandoc"
    assert called["cmd"][1].endswith("submit_resume.md")
    assert "geometry:margin=0.5in" in called["cmd"]
    assert "fontsize=10pt" in called["cmd"]
    assert "pagestyle=empty" in called["cmd"]
    assert called["cmd"][-1].endswith("Cody_McKeon_Acme_AI_Product_Manager_CoreAPI_Resume.pdf")


def test_export_resume_pdf_requires_submit_resume(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "applications" / "beta_product_manager_6"
    app_dir.mkdir(parents=True, exist_ok=True)
    job = {"id": 6, "company": "Beta", "title": "Product Manager"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.subprocess.run", lambda *args, **kwargs: None)
    main(["export-resume-pdf", "6"])
    output = capsys.readouterr().out
    assert "Missing submit_resume.md. Run prep-application <job_id> first." in output


def test_submit_resume_does_not_include_internal_sections(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "base_resume.md").write_text(
        "# Cody McKeon\n\n## Professional Summary\nBuilder summary.\n\nSecond paragraph.\n\n## Core Skills\n- Skill A\n- Skill B\n\n## Tools & Platforms\n- Tool A\n- Tool B\n",
        encoding="utf-8",
    )
    job = {
        "id": 31,
        "title": "Product Manager",
        "company": "Gamma",
        "source": "lever",
        "url": "https://example.com/job/31",
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
    main(["prep-application", "31"])
    submit_text = (tmp_path / "applications" / "gamma_product_manager_31" / "submit_resume.md").read_text(encoding="utf-8")
    for forbidden in ["Tailored Resume Draft", "Positioning", "Tailored Summary", "Experience Highlights", "Targeted Value", "Notes", "Resume Rules Applied", "[insert metric if available]"]:
        assert forbidden not in submit_text
    assert "Builder summary." in submit_text
    assert submit_text.startswith("# Cody McKeon\n")
    assert "760-669-9343 | mckeonc0827@gmail.com | https://github.com/cody-mckeon" in submit_text
    assert "**Technical Product Manager | AI Workflows | Product Systems | Agentic Operations**" in submit_text
    assert "Second paragraph." not in submit_text
    assert "## Core Skills" in submit_text
    assert "\n\n## Core Skills\n\n" in submit_text
    core_skills_body = _section_body(submit_text, "Core Skills")
    assert core_skills_body == "Skill A, Skill B"
    assert ", " in core_skills_body
    assert not any(line.startswith("- ") or line.startswith("•") for line in core_skills_body.splitlines())

    assert "## Tools & Platforms" in submit_text
    assert "\n\n## Tools & Platforms\n\n" in submit_text
    tools_body = _section_body(submit_text, "Tools & Platforms")
    assert tools_body == "Tool A, Tool B"
    assert ", " in tools_body
    assert not any(line.startswith("- ") or line.startswith("•") for line in tools_body.splitlines())
    assert not re.search(r"[^\n][ \t]+##\s+(Core Skills|Tools & Platforms|Professional Experience)", submit_text)
    assert "Experience Highlights" not in submit_text


def test_normalize_submit_resume_section_spacing_and_inline_lists():
    raw_resume = (
        "## Professional Summary\nSummary line.\n\n"
        "## Core Skills\nSkill A, Skill B"
        " ## Tools & Platforms\nTool A, Tool B"
        "\n## Professional Experience\nCompany Role\n\n"
        "## Projects\nProject details\n\n"
        "## Education\nSchool details\n"
    )
    normalized = _normalize_submit_resume(raw_resume)
    assert "\n\n## Core Skills\n\n" in normalized
    assert "\n\n## Tools & Platforms\n\n" in normalized
    assert "\n\n## Professional Experience\n\n" in normalized
    assert not re.search(r"[^\n][ \t]+##\s+(Core Skills|Tools & Platforms|Professional Experience)", normalized)
    assert _section_body(normalized, "Core Skills") == "Skill A, Skill B"
    assert _section_body(normalized, "Tools & Platforms") == "Tool A, Tool B"
    assert not any(line.startswith("- ") for line in _section_body(normalized, "Core Skills").splitlines())
    assert not any(line.startswith("- ") for line in _section_body(normalized, "Tools & Platforms").splitlines())


def test_export_resume_pdf_rejects_forbidden_internal_phrases(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "applications" / "acme_product_manager_7"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "submit_resume.md").write_text("# Resume\n\n## Notes\n", encoding="utf-8")
    job = {"id": 7, "company": "Acme", "title": "Product Manager"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    called = {"ran": False}
    monkeypatch.setattr("job_fit_agent.main.subprocess.run", lambda *args, **kwargs: called.__setitem__("ran", True))
    main(["export-resume-pdf", "7"])
    output = capsys.readouterr().out
    assert "submit_resume.md contains forbidden internal content" in output
    assert called["ran"] is False


def test_export_resume_pdf_check_can_assert_generated_pdf_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "applications" / "acme_product_manager_70"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "submit_resume.md").write_text("# Resume", encoding="utf-8")
    job = {"id": 70, "company": "Acme", "title": "Product Manager"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)

    def fake_run(cmd, check):
        output_path = Path(cmd[-1])
        output_path.write_bytes(b"%PDF-1.7\nmock")

    monkeypatch.setattr("job_fit_agent.main.subprocess.run", fake_run)
    main(["export-resume-pdf", "70"])
    pdf_path = app_dir / "Cody_McKeon_Acme_Product_Manager_Resume.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0


def test_export_resume_pdf_output_includes_cody_name(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "applications" / "acme_product_manager_71"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "submit_resume.md").write_text("# Cody McKeon\n\n## Professional Summary\nSummary\n", encoding="utf-8")
    job = {"id": 71, "company": "Acme", "title": "Product Manager"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)

    def fake_run(cmd, check):
        output_path = Path(cmd[-1])
        output_path.write_text("%PDF-1.7\nCody McKeon\n", encoding="utf-8")

    monkeypatch.setattr("job_fit_agent.main.subprocess.run", fake_run)
    main(["export-resume-pdf", "71"])
    pdf_path = app_dir / "Cody_McKeon_Acme_Product_Manager_Resume.pdf"
    assert "Cody McKeon" in pdf_path.read_text(encoding="utf-8")




def _section_body(markdown_text: str, section_name: str) -> str:
    lines = markdown_text.splitlines()
    heading = f"## {section_name}"
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == heading:
            start = idx + 1
            break
    if start is None:
        return ""
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while end < len(lines) and not lines[end].startswith("## "):
        end += 1
    return "\n".join(lines[start:end]).strip()

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

def test_digest_excludes_high_fit_skip_from_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "id": 10,
                "score": 95,
                "status": "new",
                "classification": "high_fit",
                "viability_level": "skip",
                "geographic_eligibility": "eligible",
                "title": "EU role",
                "company": "acme",
                "source": "ashby",
                "url": "https://example.com/10",
                "viability_reasons": '["hard_geo"]',
                "red_flags": "[]",
            }
        ] if classification == "high_fit" else [],
    )
    main(["digest"])
    output = capsys.readouterr().out
    assert "EU role" not in output


def test_digest_excludes_near_fit_ineligible_from_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "id": 11,
                "score": 78,
                "status": "new",
                "classification": "near_fit",
                "viability_level": "review",
                "geographic_eligibility": "ineligible",
                "title": "UK Remote PM",
                "company": "acme",
                "source": "ashby",
                "url": "https://example.com/11",
                "viability_reasons": '["uk_only"]',
                "red_flags": "[]",
            }
        ] if classification == "near_fit" else [],
    )
    main(["digest"])
    output = capsys.readouterr().out
    assert "UK Remote PM" not in output


def test_digest_tam_us_remote_is_not_actionable_by_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [{
            "id": 31, "score": 62, "status": "new", "classification": "near_fit", "viability_level": "apply_now",
            "geographic_eligibility": "eligible", "title": "Technical Account Manager", "company": "acme", "source": "x",
            "url": "https://example.com/31", "viability_reasons": "[]", "red_flags": "[]",
        }] if classification == "near_fit" else [],
    )
    main(["digest"])
    assert "Technical Account Manager" not in capsys.readouterr().out


def test_digest_product_marketing_needs_ai_or_product_systems_overlap(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [{
            "id": 32, "score": 70, "status": "new", "classification": "near_fit", "viability_level": "review",
            "geographic_eligibility": "eligible", "title": "Product Marketing Manager", "company": "acme", "source": "x",
            "url": "https://example.com/32", "viability_reasons": "[]", "red_flags": "[]",
        }] if classification == "near_fit" else [],
    )
    main(["digest"])
    assert "Product Marketing Manager" not in capsys.readouterr().out


def test_digest_tpm_internal_systems_is_actionable_near_fit(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [{
            "id": 33, "score": 65, "status": "new", "classification": "near_fit", "viability_level": "apply_now",
            "geographic_eligibility": "eligible", "title": "Technical Program Manager, Internal Systems", "company": "acme", "source": "x",
            "url": "https://jobs.lever.co/acme/33", "viability_reasons": "[]", "red_flags": "[]",
        }] if classification == "near_fit" else [],
    )
    main(["digest"])
    assert "Technical Program Manager, Internal Systems" in capsys.readouterr().out


def test_digest_marketing_ops_program_manager_without_overlap_not_actionable(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [{
            "id": 34, "score": 60, "status": "new", "classification": "near_fit", "viability_level": "review",
            "geographic_eligibility": "eligible", "title": "Marketing Operations Program Manager", "company": "acme", "source": "x",
            "url": "https://example.com/34", "viability_reasons": "[]", "red_flags": "[]",
        }] if classification == "near_fit" else [],
    )
    main(["digest"])
    assert "Marketing Operations Program Manager" not in capsys.readouterr().out


def test_digest_excludes_geography_review_from_actionable_and_shows_review_section(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    def _rows(classification, limit=10):
        if classification == "high_fit":
            return [
                {"id": 12, "score": 90, "status": "new", "classification": "high_fit", "viability_level": "review", "geographic_eligibility": "review", "title": "Geo Review", "company": "a", "source": "ashby", "url": "https://jobs.lever.co/acme/12", "viability_reasons": "[]", "red_flags": "[]"},
                {"id": 13, "score": 91, "status": "new", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Eligible Role", "company": "b", "source": "ashby", "url": "https://jobs.lever.co/acme/13", "viability_reasons": "[]", "red_flags": "[]"},
            ]
        return []
    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", _rows)
    main(["digest"])
    output = capsys.readouterr().out
    actionable_section = output.split("High role fit but geography review")[0]
    assert "Geo Review" not in actionable_section
    assert "High role fit but geography review" in output
    assert "Geo Review" in output
    assert "Eligible Role" in output


def test_digest_include_skipped_prints_separate_section(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {"id": 14, "score": 97, "status": "new", "classification": classification, "viability_level": "skip", "geographic_eligibility": "ineligible", "title": "Foster City Hybrid", "company": "x", "source": "gh", "location_raw": "Foster City, CA (Hybrid)", "url": "https://example.com/14", "viability_reasons": '["onsite_required"]', "red_flags": "[]"},
        ] if classification == "high_fit" else [],
    )
    main(["digest", "--include-skipped"])
    output = capsys.readouterr().out
    assert "Skipped jobs due to hard constraints" in output
    assert "Foster City Hybrid" in output


def test_digest_excludes_applied_rejected_archived_from_default(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    def _rows(classification, limit=10):
        if classification != "high_fit":
            return []
        return [
            {"id": 21, "score": 90, "status": "applied", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Applied Role", "company": "a", "source": "x", "url": "https://example.com/21", "viability_reasons": "[]", "red_flags": "[]"},
            {"id": 22, "score": 90, "status": "rejected", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Rejected Role", "company": "a", "source": "x", "url": "https://example.com/22", "viability_reasons": "[]", "red_flags": "[]"},
            {"id": 23, "score": 90, "status": "archived", "classification": "high_fit", "viability_level": "apply_now", "geographic_eligibility": "eligible", "title": "Archived Role", "company": "a", "source": "x", "url": "https://example.com/23", "viability_reasons": "[]", "red_flags": "[]"},
        ]
    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", _rows)
    main(["digest"])
    output = capsys.readouterr().out
    assert "Applied Role" not in output
    assert "Rejected Role" not in output
    assert "Archived Role" not in output


def test_digest_can_show_lever_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.get_top_jobs_by_classification",
        lambda classification, limit=10: [
            {
                "score": 91,
                "id": 9,
                "status": "new",
                "classification": "high_fit",
                "viability_level": "apply_now",
                "title": "Senior Product Manager",
                "company": "ramp",
                "source": "lever",
                "url": "https://jobs.lever.co/ramp/abc123",
                "location_raw": "Remote (US)",
                "normalized_location_type": "remote",
                "geographic_eligibility": "eligible",
                "viability_reasons": "[]",
                "red_flags": "[]",
            }
        ]
        if classification == "high_fit"
        else [],
    )

    main(["digest"])
    output = capsys.readouterr().out

    assert "Actionable high-fit jobs" in output
    assert "source: lever" in output
    assert "company: ramp" in output
    assert "url: https://jobs.lever.co/ramp/abc123" in output


def test_extract_application_questions_from_html(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("x", encoding="utf-8")
    job = {"id": 1, "title": "PM", "company": "Acme", "url": "https://example.com/apply"}
    html = """
    <form>
      <label for='motivation'>What motivates you professionally?</label>
      <textarea id='motivation' required></textarea>
      <label for='linkedin'>LinkedIn URL</label>
      <input id='linkedin' />
      <label for='influence'>Who are your biggest professional influences?</label>
      <select id='influence' required><option>A</option></select>
    </form>
    """
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    class Resp:
        text = html
        def raise_for_status(self): return None
    monkeypatch.setattr("job_fit_agent.main.requests.get", lambda *args, **kwargs: Resp())
    main(["extract-application-questions", "1"])
    path = tmp_path / "applications" / "acme_pm_1" / "application_questions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    questions = data["questions"]
    assert any(q["question"] == "What motivates you professionally?" and q["field_type"] == "textarea" and q["required"] is True for q in questions)
    assert any(q["question"] == "LinkedIn URL" and q["field_type"] == "input" for q in questions)
    assert any(q["question"] == "Who are your biggest professional influences?" and q["field_type"] == "select" and q["required"] is True for q in questions)


def test_extract_application_questions_no_results_message(monkeypatch, capsys):
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: {"id": 1, "title": "PM", "company": "Acme", "url": "https://example.com"})
    class Resp:
        text = "<html><body>hello</body></html>"
        def raise_for_status(self): return None
    monkeypatch.setattr("job_fit_agent.main.requests.get", lambda *args, **kwargs: Resp())
    main(["extract-application-questions", "1"])
    assert "No application questions found in static HTML." in capsys.readouterr().out


def test_add_application_question_creates_file_and_avoids_duplicates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job = {"id": 2, "title": "PM", "company": "Beta", "url": "https://example.com"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    main(["add-application-question", "2", "Why this role?"])
    main(["add-application-question", "2", "Why this role?"])
    path = tmp_path / "applications" / "beta_pm_2" / "application_questions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert len(data["questions"]) == 1
    assert data["questions"][0]["source"] == "manual"


def test_generate_application_answers_from_saved_questions(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job = {"id": 3, "title": "PM", "company": "Gamma", "url": "https://example.com"}
    app_dir = tmp_path / "applications" / "gamma_pm_3"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text("questions:\n  - question: \"Why this company?\"\n    source: manual\n", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    main(["generate-application-answers", "3"])
    output = (app_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "## Why this company?" in output


def test_generate_application_answers_excludes_standard_fields(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job = {"id": 33, "title": "PM", "company": "Gamma", "url": "https://example.com"}
    app_dir = tmp_path / "applications" / "gamma_pm_33"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text(
        "questions:\n"
        "  - question: \"First Name\"\n"
        "    source: browser\n"
        "    is_standard_field: true\n"
        "  - question: \"Why this role?\"\n"
        "    source: browser\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    main(["generate-application-answers", "33"])
    output = (app_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "## First Name" not in output
    assert "## Why this role?" in output


def test_extract_application_questions_browser_with_debug(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job = {"id": 50, "title": "PM", "company": "Acme", "url": "https://example.com/apply"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)

    class FakeLocator:
        def count(self):
            return 1
        def filter(self, **kwargs):
            return self
        @property
        def first(self):
            return self
        def scroll_into_view_if_needed(self):
            return None
        def click(self):
            return None

    class FakePage:
        url = "https://example.com/apply/form"
        def goto(self, *args, **kwargs):
            return None
        def wait_for_load_state(self, *args, **kwargs):
            return None
        def evaluate(self, *args, **kwargs):
            return None
        def wait_for_timeout(self, *args, **kwargs):
            return None
        def wait_for_selector(self, *args, **kwargs):
            return None
        def get_by_role(self, *args, **kwargs):
            return FakeLocator()
        def locator(self, *args, **kwargs):
            return FakeLocator()
        def content(self):
            return "<form><label for='q1'>Why this role?</label><textarea id='q1' required></textarea></form>"
        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

    class FakeBrowser:
        def new_page(self):
            return FakePage()
        def close(self):
            return None

    class FakePlaywright:
        def __enter__(self):
            class C:
                chromium = type("Chromium", (), {"launch": lambda self, headless=True: FakeBrowser()})()
            return C()
        def __exit__(self, exc_type, exc, tb):
            return False

    sync_api_module = types.SimpleNamespace(sync_playwright=lambda: FakePlaywright())
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=sync_api_module))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)
    main(["extract-application-questions-browser", "50", "--debug"])
    app_dir = tmp_path / "applications" / "acme_pm_50"
    assert (app_dir / "application_questions.yaml").exists()
    assert (app_dir / "browser_debug_snapshot.html").exists()
    assert (app_dir / "browser_debug_screenshot.png").exists()
    data = yaml.safe_load((app_dir / "application_questions.yaml").read_text(encoding="utf-8"))
    assert any(q["question"] == "Why this role?" and q["source"] == "browser" for q in data["questions"])


def test_extract_application_questions_browser_failure_includes_stage_and_exception(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    job = {"id": 51, "title": "PM", "company": "Acme", "url": "https://example.com/apply"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)

    class EmptyLocator:
        def count(self):
            return 0
        def filter(self, **kwargs):
            return self
        @property
        def first(self):
            return self
        def scroll_into_view_if_needed(self):
            return None
        def click(self):
            return None

    class FakePage:
        url = "https://example.com/apply"
        def goto(self, *args, **kwargs):
            return None
        def wait_for_load_state(self, *args, **kwargs):
            return None
        def evaluate(self, *args, **kwargs):
            return None
        def wait_for_timeout(self, *args, **kwargs):
            return None
        def wait_for_selector(self, *args, **kwargs):
            return None
        def get_by_role(self, *args, **kwargs):
            return EmptyLocator()
        def locator(self, *args, **kwargs):
            return EmptyLocator()
        def content(self):
            return "<html></html>"
        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

    class FakeBrowser:
        def new_page(self):
            return FakePage()
        def close(self):
            return None

    class FakePlaywright:
        def __enter__(self):
            class C:
                chromium = type("Chromium", (), {"launch": lambda self, headless=True: FakeBrowser()})()
            return C()
        def __exit__(self, exc_type, exc, tb):
            return False

    sync_api_module = types.SimpleNamespace(sync_playwright=lambda: FakePlaywright())
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=sync_api_module))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    main(["extract-application-questions-browser", "51", "--debug"])
    out = capsys.readouterr().out
    assert "stage=apply_button_found" in out
    assert "exception_type=RuntimeError" in out
    assert "exception_message=No apply button selector matched" in out


def test_extract_application_questions_browser_clicks_ashby_apply_button(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    job = {"id": 52, "title": "PM", "company": "Acme", "url": "https://example.com/apply"}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)

    attempted = []
    fixture_html = (Path(__file__).parent / "fixtures" / "ashby" / "apply_button.html").read_text(encoding="utf-8")

    class FakeLocator:
        def __init__(self, selector, count_value=0):
            self.selector = selector
            self._count = count_value
        def count(self):
            return self._count
        def filter(self, **kwargs):
            attempted.append(("filter", self.selector, kwargs))
            return self
        @property
        def first(self):
            return self
        def scroll_into_view_if_needed(self):
            attempted.append(("scroll", self.selector))
            return None
        def click(self):
            attempted.append(("click", self.selector))
            return None

    class FakePage:
        url = "https://example.com/apply/form"
        def goto(self, *args, **kwargs):
            return None
        def wait_for_load_state(self, *args, **kwargs):
            return None
        def evaluate(self, *args, **kwargs):
            return None
        def wait_for_timeout(self, *args, **kwargs):
            return None
        def wait_for_selector(self, *args, **kwargs):
            return None
        def get_by_role(self, role, name=None):
            attempted.append(("role", role, str(name.pattern if hasattr(name, "pattern") else name)))
            if role == "button" and hasattr(name, "pattern") and "apply for this job" in name.pattern.lower():
                return FakeLocator("role:button exact apply for this job", 1)
            return FakeLocator("role:button apply", 0)
        def locator(self, selector):
            attempted.append(selector)
            return FakeLocator(selector, 0)
        def content(self):
            return fixture_html + "<form><label for='q1'>Why this role?</label><textarea id='q1' required></textarea></form>"
        def screenshot(self, path, full_page=True):
            Path(path).write_bytes(b"png")

    class FakeBrowser:
        def new_page(self):
            return FakePage()
        def close(self):
            return None

    class FakePlaywright:
        def __enter__(self):
            class C:
                chromium = type("Chromium", (), {"launch": lambda self, headless=True: FakeBrowser()})()
            return C()
        def __exit__(self, exc_type, exc, tb):
            return False

    sync_api_module = types.SimpleNamespace(sync_playwright=lambda: FakePlaywright())
    monkeypatch.setitem(sys.modules, "playwright", types.SimpleNamespace(sync_api=sync_api_module))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api_module)

    main(["extract-application-questions-browser", "52"])
    assert ("click", "role:button exact apply for this job") in attempted



def test_wait_for_application_form_waits_on_textarea(monkeypatch):
    from job_fit_agent.main import wait_for_application_form

    calls = []

    class P:
        def wait_for_selector(self, selector, **kwargs):
            calls.append(selector)
            return None

    strategy = wait_for_application_form(P())
    assert strategy == "css_form_fields"
    assert calls == ["textarea, input, select, form, label"]


def test_wait_for_application_form_waits_on_input(monkeypatch):
    from job_fit_agent.main import wait_for_application_form

    class P:
        def wait_for_selector(self, selector, **kwargs):
            assert selector == "textarea, input, select, form, label"
            return None

    assert wait_for_application_form(P()) == "css_form_fields"


def test_wait_for_application_form_falls_back_to_question_text():
    from job_fit_agent.main import wait_for_application_form

    class Locator:
        @property
        def first(self):
            return self
        def wait_for(self, timeout=0):
            return None

    class P:
        def wait_for_selector(self, selector, **kwargs):
            raise TimeoutError("css timed out")
        def get_by_text(self, pattern):
            assert pattern.pattern == r"\?"
            return Locator()

    assert wait_for_application_form(P()) == "question_text"


def test_wait_for_application_form_does_not_use_invalid_mixed_selector():
    from job_fit_agent.main import wait_for_application_form

    selectors = []

    class Locator:
        @property
        def first(self):
            return self
        def wait_for(self, timeout=0):
            raise TimeoutError("no text")

    class P:
        def wait_for_selector(self, selector, **kwargs):
            selectors.append(selector)
            raise TimeoutError("css timed out")
        def get_by_text(self, pattern):
            return Locator()

    try:
        wait_for_application_form(P())
    except RuntimeError:
        pass
    assert selectors == ["textarea, input, select, form, label"]
    assert "text=/" not in selectors[0]


def test_wait_for_application_form_failure_raises_clear_runtime_error():
    from job_fit_agent.main import wait_for_application_form

    class Locator:
        @property
        def first(self):
            return self
        def wait_for(self, timeout=0):
            raise TimeoutError("missing")

    class P:
        def wait_for_selector(self, selector, **kwargs):
            raise TimeoutError("css timed out")
        def get_by_text(self, pattern):
            return Locator()

    with pytest.raises(RuntimeError, match="Application form did not become visible after clicking Apply"):
        wait_for_application_form(P())


def test_prep_application_creates_answer_bank_and_no_answers_without_questions(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    job = {"id": 4, "title": "PM", "company": "Delta", "source": "ashby", "url": "https://example.com", "score": 70, "classification": "near_fit", "viability_level": "review", "location_raw": "Remote", "location": "Remote", "geographic_eligibility": "eligible", "reasons": "[]", "red_flags": "[]", "viability_reasons": "[]", "status": "new", "notes": ""}
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda *_: None)
    main(["prep-application", "4"])
    app_dir = tmp_path / "applications" / "delta_pm_4"
    assert (app_dir / "answer_bank.md").exists()
    assert (app_dir / "application_answers.md").exists() is False


def test_standard_field_detection_expanded():
    from job_fit_agent.main import _is_standard_field
    assert _is_standard_field("Name") is True
    assert _is_standard_field("GitHub") is True
    assert _is_standard_field("Twitter Handle") is True
    assert _is_standard_field("Portfolio") is True
    assert _is_standard_field("What country are you based in?") is True


def test_yes_no_radio_options_not_answerable_and_group_prompt_extracted():
    from bs4 import BeautifulSoup
    from job_fit_agent.main import _extract_application_questions_from_soup

    html = """
    <form>
      <fieldset>
        <legend>Are you legally authorized to work?</legend>
        <label><input type='radio' name='auth' value='yes'/>Yes</label>
        <label><input type='radio' name='auth' value='no'/>No</label>
      </fieldset>
    </form>
    """
    qs = _extract_application_questions_from_soup(BeautifulSoup(html, 'html.parser'), 'https://example.com')
    assert len(qs) == 1
    assert qs[0]["question"] == "Are you legally authorized to work?"
    assert qs[0]["answerable"] is False


def test_linear_ai_product_feature_textarea_is_answerable():
    from bs4 import BeautifulSoup
    from job_fit_agent.main import _extract_application_questions_from_soup

    prompt = "Describe an AI-powered product feature you’ve recently shipped. Which techniques and technologies were used to build the feature? How did you evaluate the outcome quality?"
    html = f"<form><label for='q1'>{prompt}</label><textarea id='q1'></textarea></form>"
    qs = _extract_application_questions_from_soup(BeautifulSoup(html, 'html.parser'), 'https://example.com')
    assert len(qs) == 1
    assert qs[0]["question"] == prompt
    assert qs[0]["answerable"] is True


def test_generate_application_answers_only_answerable_true(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job = {"id": 77, "title": "PM", "company": "Delta", "url": "https://example.com"}
    app_dir = tmp_path / "applications" / "delta_pm_77"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text(
        """questions:
  - question: "GitHub"
    source: browser
    is_standard_field: true
    answerable: false
  - question: "Why this role?"
    source: browser
    answerable: true
  - question: "Yes"
    source: browser
    field_type: radio
    answerable: false
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    from job_fit_agent.main import main

    main(["generate-application-answers", "77"])
    output = (app_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "## Why this role?" in output
    assert "## GitHub" not in output
    assert "## Yes" not in output


def test_generate_application_answers_ai_feature_is_concrete_job_fit_agent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job = {"id": 88, "title": "PM", "company": "linear", "url": "https://example.com"}
    app_dir = tmp_path / "applications" / "linear_pm_88"
    app_dir.mkdir(parents=True, exist_ok=True)
    prompt = "Describe an AI-powered product feature you’ve recently shipped. Which techniques and technologies were used to build the feature? How did you evaluate the outcome quality?"
    (app_dir / "application_questions.yaml").write_text(
        f'questions:\n  - question: "{prompt}"\n    source: browser\n    answerable: true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job)
    from job_fit_agent.main import main

    main(["generate-application-answers", "88"])
    output = (app_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "Job Fit Agent" in output
    assert "Python" in output
    assert any(token in output for token in ["SQLite", "GitHub Actions", "Telegram"])
    assert "quality" in output.lower()
    assert "human-in-the-loop" in output.lower()
    assert "Based on my background" not in output
    assert "I would answer this" not in output
    assert "confirm exact years" not in output
    assert "Linear" in output
    assert "%" not in output


def _make_sqlite_job_row(**overrides):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("create table jobs (id integer, title text, company text, url text)")
    base = {"id": 101, "title": "PM", "company": "Delta", "url": "https://example.com"}
    base.update(overrides)
    conn.execute("insert into jobs (id, title, company, url) values (?, ?, ?, ?)", (base["id"], base["title"], base["company"], base["url"]))
    return conn.execute("select * from jobs").fetchone(), conn


def test_generate_application_answers_supports_sqlite_row_job_record(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job_row, conn = _make_sqlite_job_row(id=201, title="PM", company="Delta")
    app_dir = tmp_path / "applications" / "delta_pm_201"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text(
        'questions:\n  - question: "Why this role?"\n    source: browser\n    answerable: true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job_row)
    from job_fit_agent.main import main

    main(["generate-application-answers", "201"])
    output = (app_dir / "application_answers.md").read_text(encoding="utf-8")
    assert "## Why this role?" in output
    conn.close()


def test_generate_application_answers_missing_company_on_sqlite_row_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job_row, conn = _make_sqlite_job_row(id=202, title="PM", company=None)
    app_dir = tmp_path / "applications" / "company_pm_202"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text(
        'questions:\n  - question: "Why this role?"\n    source: browser\n    answerable: true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job_row)
    from job_fit_agent.main import main

    main(["generate-application-answers", "202"])
    assert (app_dir / "application_answers.md").exists()
    conn.close()


def test_generate_application_answers_creates_markdown_file_for_sqlite_row(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "profile").mkdir(parents=True, exist_ok=True)
    (tmp_path / "profile" / "base_resume.md").write_text("- resume", encoding="utf-8")
    (tmp_path / "profile" / "profile_context.yaml").write_text("strengths:\n - execution\n", encoding="utf-8")
    job_row, conn = _make_sqlite_job_row(id=203, title="PM", company="Delta")
    app_dir = tmp_path / "applications" / "delta_pm_203"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "application_questions.yaml").write_text(
        'questions:\n  - question: "Tell us about a project"\n    source: browser\n    answerable: true\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda _: job_row)
    from job_fit_agent.main import main

    main(["generate-application-answers", "203"])
    assert (app_dir / "application_answers.md").exists() is True
    conn.close()


def test_digest_adds_automation_ai_operations_review_section(monkeypatch, capsys) -> None:
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)

    def _rows(classification, limit=10):
        if classification == "near_fit":
            return [{
                "id": 44,
                "score": 72,
                "status": "new",
                "classification": "near_fit",
                "viability_level": "apply_now",
                "geographic_eligibility": "eligible",
                "title": "Business Systems Manager",
                "company": "acme",
                "source": "lever",
                "url": "https://jobs.lever.co/acme/44",
                "role_family": "business_systems",
                "reasons": '["Role-family match pairs a realistic market title with AI, automation, workflow, or internal systems context."]',
                "viability_reasons": "[]",
                "red_flags": "[]",
            }]
        return []

    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", _rows)
    main(["digest"])
    output = capsys.readouterr().out
    assert "Automation / AI operations roles worth reviewing" in output
    assert "Business Systems Manager" in output


def test_digest_separates_eligible_ineligible_and_review_jobs(monkeypatch, capsys) -> None:
    base = {
        "score": 90,
        "status": "new",
        "application_status": "not_applied",
        "classification": "high_fit",
        "viability_level": "apply_now",
        "source": "ashby",
        "company": "acme",
        "viability_reasons": "[]",
        "red_flags": "[]",
    }
    high_rows = [
        base | {"id": 301, "title": "Remote United States Enterprise Solutions Engineer", "geographic_eligibility": "eligible", "url": "https://jobs.ashbyhq.com/acme/geo-eligible"},
        base | {"id": 302, "title": "DACH Forward Deployed Engineer", "geographic_eligibility": "ineligible", "geographic_reason": "International region detected: DACH", "url": "https://jobs.ashbyhq.com/acme/geo-ineligible"},
        base | {"id": 303, "title": "North America Forward Deployed Engineer", "geographic_eligibility": "review", "url": "https://jobs.ashbyhq.com/acme/geo-review"},
    ]
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_top_jobs_by_classification", lambda classification, limit=50: high_rows if classification == "high_fit" else [])
    main(["digest"])
    output = capsys.readouterr().out
    assert "Actionable apply-now roles" in output
    assert "Remote United States Enterprise Solutions Engineer" in output.split("Strong role fit, geography not eligible")[0]
    assert "Strong role fit, geography not eligible" in output
    assert "DACH Forward Deployed Engineer" in output.split("Needs geography review")[0]
    assert "Needs geography review" in output
    assert "North America Forward Deployed Engineer" in output.split("Needs geography review", 1)[1]


def test_lennar_product_manager_resume_tailoring_uses_ai_native_pm_language(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    base_resume = Path(__file__).resolve().parents[1] / "profile" / "base_resume.md"
    (profile_dir / "base_resume.md").write_text(base_resume.read_text(encoding="utf-8"), encoding="utf-8")
    (profile_dir / "profile_context.yaml").write_text("strengths:\n  - digital product ownership\n  - product analytics\n  - customer journey\n", encoding="utf-8")
    (profile_dir / "resume_rules.yaml").write_text("rules:\n  - Avoid unsupported metrics\n", encoding="utf-8")
    job = _prep_job(
        "Product Manager, Digital Buying & Selling",
        company="Lennar",
        role_family="product_manager",
        notes="digital buying and selling product manager responsible for product discovery, requirements, experimentation, conversion optimization, customer journey, internal sales tools, analytics, and stakeholder alignment",
    )
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "88"])

    app_dir = tmp_path / "applications" / "lennar_product_manager_digital_buying_selling_88"
    resume_text = (app_dir / "resume_draft.md").read_text(encoding="utf-8")
    strategy_text = (app_dir / "resume_strategy.md").read_text(encoding="utf-8")
    assert "Technical Product Manager | AI-Powered Digital Products | Product Analytics" in strategy_text
    assert "Technical Product Manager | AI-Powered Digital Products | Product Analytics | Customer Journey" not in strategy_text
    assert "Technical Product Manager building AI-powered digital products" in resume_text
    assert "customer-facing experiences" in resume_text
    for term in [
        "Product Discovery",
        "Product Requirements",
        "User Behavior",
        "Conversion Optimization",
        "AI-assisted Product Development",
    ]:
        assert term in resume_text or term in strategy_text
    assert "## Product Methodologies" in resume_text
    assert "## Product Methodologies / Product Skills" not in resume_text
    product_methodologies = _section_body(resume_text, "Product Methodologies")
    assert product_methodologies == (
        "Product Roadmap, Product Discovery, Feature Prioritization, User Behavior Analysis, "
        "Conversion Optimization, Customer Journey Mapping, Experimentation, A/B Testing, "
        "Product Requirements, User Stories, Backlog Prioritization, Product Lifecycle, "
        "Stakeholder Alignment, AI-assisted Product Development"
    )
    assert resume_text.find("## Core Skills") < resume_text.find("## Product Methodologies") < resume_text.find("## Tools & Platforms")
    submit_text = (app_dir / "submit_resume.md").read_text(encoding="utf-8")
    assert submit_text.startswith("# Cody McKeon")
    for forbidden in [
        "Tailored Resume Draft",
        "Positioning",
        "Tailored Summary",
        "Experience Highlights",
        "Selected Projects",
        "Targeted Value",
        "Notes",
        "Resume Rules Applied",
    ]:
        assert forbidden not in submit_text
    assert "**Technical Product Manager | AI-Powered Digital Products | Product Analytics**" in submit_text
    assert "Project Manager, Marketing | Digital Experience & AI Enablement" in submit_text
    assert "Technical Product Manager, Digital Experience & AI Enablement" not in submit_text
    assert "## Product Methodologies" in submit_text
    assert _section_body(submit_text, "Product Methodologies") == product_methodologies
    assert submit_text.find("## Core Skills") < submit_text.find("## Product Methodologies") < submit_text.find("## Tools & Platforms")
    tools_platforms = _section_body(submit_text, "Tools & Platforms")
    assert tools_platforms == (
        "OpenClaw, Hermes Agent, GPT-5.5, OpenAI API, local LLMs, Qwen 3, Python, "
        "GitHub / GitHub Actions, SQLite, Telegram Bot API, Asana, Pendo, GA4, "
        "Google Tag Manager, OneTrust, Figma, pytest"
    )
    assert all(tool in tools_platforms for tool in ["Hermes Agent", "local LLMs", "Qwen 3"])
    assert tools_platforms.count("\n") == 0
    assert submit_text.count("## Projects") == 1
    assert submit_text.find("## Professional Experience") < submit_text.find("## Projects") < submit_text.find("## Education")
    assert "Built AI-assisted workflows to accelerate product discovery" in resume_text
    assert "implementation-ready product requirements" in resume_text
    assert "AI Marketing Intelligence Platform" in resume_text
    assert "Marketing Intelligence OS:" not in resume_text
    assert resume_text.find("AI Marketing Intelligence Platform") < resume_text.find("Job Fit Agent")
    for subtitle in [
        "AI-assisted analytics, reporting operations, behavioral insights, product decision support",
        "AI-assisted product discovery, UX decision support, concept validation, requirements generation",
        "AI product scoring, workflow automation, decision systems, application operations",
        "AI-assisted prioritization, stakeholder alignment, operational decision support",
    ]:
        assert subtitle in submit_text
    for tool_heavy_subtitle in [
        "AI Workflow Design, Marketing Analytics, Reporting Operations, Use-Case Prioritization, Workflow Automation",
        "Python, Markdown Agents, Figma, Product Analytics, Design Systems, Workflow Automation, Evaluation Frameworks",
        "Python, SQLite, GitHub Actions, Telegram Bot API, Ashby, Greenhouse, pytest",
        "OpenClaw, Hermes Agent, GPT-5.5, OpenAI API, local LLMs, Qwen 3, Telegram, Asana, Python, Markdown Configuration",
    ]:
        assert tool_heavy_subtitle not in submit_text
    for tool in ["Hermes Agent", "local LLMs", "Qwen 3"]:
        assert tool in resume_text
        assert tool in submit_text
    assert "model training" not in resume_text.lower()
    assert "ml infrastructure" not in resume_text.lower()
