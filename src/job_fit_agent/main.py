"""CLI entry point for job-fit-agent."""

from __future__ import annotations

import logging

from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.scoring import score_job

LOGGER = logging.getLogger(__name__)
COMPANIES = ["stripe", "duolingo", "notion"]


def collect_ranked_jobs(
    collector: GreenhouseCollector,
    companies: list[str] | None = None,
    min_score: int = 60,
) -> list[tuple[JobPosting, FitScore]]:
    """Fetch, score, threshold, and rank jobs across companies."""
    selected_companies = companies or COMPANIES
    ranked_jobs: list[tuple[JobPosting, FitScore]] = []

    for company in selected_companies:
        try:
            jobs = collector.fetch_jobs(company)
        except Exception as exc:  # pragma: no cover - defensive guardrail for collectors
            LOGGER.warning("Failed to fetch jobs for %s: %s", company, exc)
            continue
        for job in jobs:
            fit = score_job(job)
            if fit.total_score >= min_score:
                ranked_jobs.append((job, fit))

    ranked_jobs.sort(key=lambda item: item[1].total_score, reverse=True)
    return ranked_jobs


def main() -> None:
    collector = GreenhouseCollector()
    selected_companies = COMPANIES
    ranked_jobs: list[tuple[JobPosting, FitScore]] = []
    jobs_fetched = 0

    for company in selected_companies:
        try:
            jobs = collector.fetch_jobs(company)
        except Exception as exc:  # pragma: no cover - defensive guardrail for collectors
            LOGGER.warning("Failed to fetch jobs for %s: %s", company, exc)
            continue

        jobs_fetched += len(jobs)
        for job in jobs:
            fit = score_job(job)
            if fit.total_score >= 60:
                ranked_jobs.append((job, fit))

    ranked_jobs.sort(key=lambda item: item[1].total_score, reverse=True)

    for job, fit in ranked_jobs:
        print(f"score: {fit.total_score}")
        print(f"title: {job.title}")
        print(f"company: {job.company}")
        print(f"location: {job.location}")
        print(f"url: {job.url}")
        print(f"reasons: {fit.reasons}")
        print(f"red_flags: {fit.red_flags}")
        print("-" * 40)

    print("Summary")
    print(f"companies checked: {len(selected_companies)}")
    print(f"jobs fetched: {jobs_fetched}")
    print(f"jobs above threshold: {len(ranked_jobs)}")


if __name__ == "__main__":
    main()
