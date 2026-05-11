from job_fit_agent.repository import UpsertResult
from job_fit_agent.config import load_target_profile
from job_fit_agent.main import collect_ranked_jobs, collect_scored_jobs, group_jobs_by_classification, main
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
                "status": "reviewing",
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
    assert "status: reviewing" in output
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
    monkeypatch.setattr("job_fit_agent.main.print_digest", lambda: None)

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
