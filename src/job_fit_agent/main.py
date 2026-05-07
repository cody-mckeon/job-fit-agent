"""CLI entry point for job-fit-agent."""

from __future__ import annotations

import logging

from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.config import TargetProfile, load_company_watchlist, load_target_profile
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.scoring import score_job

LOGGER = logging.getLogger(__name__)


def collect_ranked_jobs(
    collector: GreenhouseCollector,
    target_profile: TargetProfile,
    companies: list[str],
    min_score: int = 45,
) -> list[tuple[JobPosting, FitScore]]:
    """Fetch, score, threshold, and rank jobs across companies."""
    ranked_jobs: list[tuple[JobPosting, FitScore]] = []

    for company in companies:
        try:
            jobs = collector.fetch_jobs(company)
        except Exception as exc:  # pragma: no cover - defensive guardrail for collectors
            LOGGER.warning("Failed to fetch jobs for %s: %s", company, exc)
            continue
        for job in jobs:
            fit = score_job(job, target_profile)
            if fit.total_score >= min_score:
                ranked_jobs.append((job, fit))

    ranked_jobs.sort(key=lambda item: item[1].total_score, reverse=True)
    return ranked_jobs


def resolve_companies(source: str = "greenhouse") -> list[str]:
    """Resolve companies from config for a given source."""
    watchlist = load_company_watchlist()
    companies = getattr(watchlist, source, [])
    if not companies:
        raise ValueError(f"No companies configured for source '{source}'. Update config/company_watchlist.yaml.")
    return companies


def main() -> None:
    collector = GreenhouseCollector()
    target_profile = load_target_profile()
    source = "greenhouse"
    selected_companies = resolve_companies(source=source)
    print(f"source: {source}")
    print(f"companies: {', '.join(selected_companies)}")

    ranked_jobs = collect_ranked_jobs(
        collector=collector,
        target_profile=target_profile,
        companies=selected_companies,
        min_score=45,
    )

    jobs_fetched = 0
    for company in selected_companies:
        try:
            jobs_fetched += len(collector.fetch_jobs(company))
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Failed to fetch jobs for %s: %s", company, exc)

    for job, fit in ranked_jobs:
        print(f"score: {fit.total_score}")
        print(f"title: {job.title}")
        print(f"company: {job.company}")
        print(f"location: {job.location}")
        print(f"url: {job.url}")
        print(f"reasons: {fit.reasons}")
        if fit.red_flags:
            print("location/fit red flags:")
            for flag in fit.red_flags:
                print(f"  - {flag}")
        else:
            print("location/fit red flags: none")
        print("-" * 40)

    print("Summary")
    print(f"companies checked: {len(selected_companies)}")
    print(f"jobs fetched: {jobs_fetched}")
    print(f"jobs above threshold: {len(ranked_jobs)}")


if __name__ == "__main__":
    main()
