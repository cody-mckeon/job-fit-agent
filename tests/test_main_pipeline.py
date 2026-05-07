from job_fit_agent.config import load_target_profile
from job_fit_agent.main import collect_ranked_jobs, collect_scored_jobs, group_jobs_by_classification, main
from job_fit_agent.models import JobPosting


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


def test_main_prints_high_fit_jobs_normally(monkeypatch, capsys) -> None:
    job = _job("Product Manager AI", location="Remote US")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [job]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"])

    main()
    output = capsys.readouterr().out

    assert "score:" in output
    assert "title: Product Manager AI" in output
    assert "high_fit count: 1" in output
    assert "near_fit count: 0" in output
    assert "No high-fit jobs found." not in output


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
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"])

    main()
    output = capsys.readouterr().out

    assert "No high-fit jobs found." in output
    assert "Near-fit jobs worth reviewing" in output
    assert "Top low-fit jobs for review" not in output


def test_main_prints_low_fit_debug_only_when_no_high_or_near(monkeypatch, capsys) -> None:
    low = _job("Software Engineer", location="On-site", description="backend systems")

    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [low]}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"])

    main()
    output = capsys.readouterr().out

    assert "No high-fit jobs found." in output
    assert "Near-fit jobs worth reviewing" not in output
    assert "Top low-fit jobs for review" in output


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
