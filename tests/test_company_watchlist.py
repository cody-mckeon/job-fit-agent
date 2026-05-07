from pathlib import Path

import pytest

from job_fit_agent import main
from job_fit_agent.config import CompanyWatchlist, load_company_watchlist, load_target_profile
from job_fit_agent.main import collect_ranked_jobs, resolve_companies
from job_fit_agent.models import JobPosting


class StubCollector:
    def __init__(self, jobs_by_company, invalid_companies=None):
        self.jobs_by_company = jobs_by_company
        self.invalid_companies = set(invalid_companies or [])

    def validate_company_token(self, company: str) -> bool:
        return company not in self.invalid_companies

    def fetch_jobs(self, company: str):
        return self.jobs_by_company.get(company, [])


def _job(title: str) -> JobPosting:
    return JobPosting(
        source="greenhouse",
        company="example",
        title=title,
        location="Remote",
        url="https://example.com/job",
        description="AI analytics",
    )


def test_company_watchlist_loads_correctly() -> None:
    watchlist = load_company_watchlist()

    assert isinstance(watchlist, CompanyWatchlist)
    assert watchlist.greenhouse[:2] == ["stripe", "duolingo"]
    assert "openai" not in watchlist.greenhouse
    assert "supabase" not in watchlist.greenhouse
    assert "anthropic" not in watchlist.ashby


def test_pipeline_accepts_company_list_from_config() -> None:
    companies = resolve_companies(source="greenhouse")
    collector = StubCollector({"stripe": [_job("Product Manager AI")], "duolingo": []})

    ranked = collect_ranked_jobs(collector=collector, target_profile=load_target_profile(), companies=companies, min_score=0)

    assert "stripe" in companies
    assert "duolingo" in companies
    assert len(companies) >= 2
    assert len(ranked) == 1


def test_missing_or_empty_watchlist_fails_gracefully(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing_path = tmp_path / "missing.yaml"
    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        load_company_watchlist(missing_path)

    monkeypatch.setattr(main, "load_company_watchlist", lambda: CompanyWatchlist(greenhouse=[]))
    with pytest.raises(ValueError, match="No companies configured for source 'greenhouse'"):
        resolve_companies(source="greenhouse")
