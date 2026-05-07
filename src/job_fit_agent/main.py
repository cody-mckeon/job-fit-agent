"""CLI entry point for job-fit-agent."""

from __future__ import annotations

import logging
from typing import Protocol

from job_fit_agent.collectors.ashby import AshbyCollector
from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.config import TargetProfile, load_company_watchlist, load_target_profile
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.scoring import score_job

LOGGER = logging.getLogger(__name__)


class JobCollector(Protocol):
    def fetch_jobs(self, company: str) -> list[JobPosting]: ...



def group_jobs_by_classification(
    scored_jobs: list[tuple[JobPosting, FitScore]],
) -> tuple[list[tuple[JobPosting, FitScore]], list[tuple[JobPosting, FitScore]], list[tuple[JobPosting, FitScore]]]:
    high_fit_jobs = [(job, fit) for job, fit in scored_jobs if fit.classification == "high_fit"]
    near_fit_jobs = [(job, fit) for job, fit in scored_jobs if fit.classification == "near_fit"]
    low_fit_jobs = [(job, fit) for job, fit in scored_jobs if fit.classification == "low_fit"]
    return high_fit_jobs, near_fit_jobs, low_fit_jobs


def collect_ranked_jobs(
    collector: JobCollector,
    target_profile: TargetProfile,
    companies: list[str],
    min_score: int = 45,
) -> list[tuple[JobPosting, FitScore]]:
    ranked_jobs, _ = collect_scored_jobs(collector, target_profile, companies, min_score)
    return ranked_jobs


def collect_scored_jobs(
    collector: JobCollector,
    target_profile: TargetProfile,
    companies: list[str],
    min_score: int = 45,
) -> tuple[list[tuple[JobPosting, FitScore]], list[tuple[JobPosting, FitScore]]]:
    ranked_jobs: list[tuple[JobPosting, FitScore]] = []
    below_threshold_jobs: list[tuple[JobPosting, FitScore]] = []

    for company in companies:
        validate = getattr(collector, "validate_company_token", None)
        if callable(validate) and not validate(company):
            continue
        try:
            jobs = collector.fetch_jobs(company)
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Failed to fetch jobs for %s: %s", company, exc)
            continue
        for job in jobs:
            fit = score_job(job, target_profile)
            if fit.total_score >= min_score:
                ranked_jobs.append((job, fit))
            else:
                below_threshold_jobs.append((job, fit))

    ranked_jobs.sort(key=lambda item: item[1].total_score, reverse=True)
    below_threshold_jobs.sort(key=lambda item: item[1].total_score, reverse=True)
    return ranked_jobs, below_threshold_jobs


def resolve_companies(source: str = "greenhouse") -> list[str]:
    watchlist = load_company_watchlist()
    companies = getattr(watchlist, source, [])
    if not companies:
        raise ValueError(f"No companies configured for source '{source}'. Update config/company_watchlist.yaml.")
    return companies


def print_jobs(section_title: str | None, jobs: list[tuple[JobPosting, FitScore]], limit: int = 15) -> None:
    if not jobs:
        return

    if section_title:
        print(section_title)
    for job, fit in sorted(jobs, key=lambda item: item[1].total_score, reverse=True)[:limit]:
        print(f"score: {fit.total_score}")
        print(f"classification: {fit.classification}")
        print(f"role_family: {fit.role_family}")
        print(f"source: {job.source}")
        print(f"title: {job.title}")
        print(f"company: {job.company}")
        print(f"location: {job.location}")
        print(f"workplace_type: {job.workplace_type}")
        print(f"department: {job.department}")
        print(f"team: {job.team}")
        print(f"url: {job.url}")
        print(f"reasons: {', '.join(fit.reasons) if fit.reasons else 'none'}")
        print(f"red_flags: {', '.join(fit.red_flags) if fit.red_flags else 'none'}")
        print("-")


def main() -> None:
    target_profile = load_target_profile()
    collectors: dict[str, JobCollector] = {
        "greenhouse": GreenhouseCollector(),
        "ashby": AshbyCollector(),
    }

    successful_by_source: dict[str, list[str]] = {}
    failed_by_source: dict[str, list[str]] = {}
    all_ranked: list[tuple[JobPosting, FitScore]] = []
    all_below: list[tuple[JobPosting, FitScore]] = []

    for source, collector in collectors.items():
        companies = resolve_companies(source=source)
        success: list[str] = []
        failed: list[str] = []

        for company in companies:
            jobs = collector.fetch_jobs(company)
            if jobs:
                success.append(company)
            else:
                failed.append(company)

        successful_by_source[source] = success
        failed_by_source[source] = failed

        ranked, below = collect_scored_jobs(collector, target_profile, success, min_score=45)
        all_ranked.extend(ranked)
        all_below.extend(below)

    all_scored_jobs = all_ranked + all_below
    high_fit_jobs, near_fit_jobs, low_fit_jobs = group_jobs_by_classification(all_scored_jobs)

    if len(high_fit_jobs) == 0:
        print("No high-fit jobs found.")
    else:
        print_jobs("High-fit jobs to review", high_fit_jobs, limit=15)

    if near_fit_jobs:
        if high_fit_jobs:
            print()
        print("Near-fit jobs worth reviewing")
        print_jobs(None, near_fit_jobs)

    if len(high_fit_jobs) == 0 and not near_fit_jobs:
        print("Top low-fit jobs for review")

    print("Summary")
    for source in collectors:
        success = successful_by_source[source]
        failed = failed_by_source[source]
        print(f"{source} successful companies: {', '.join(success) if success else 'none'}")
        print(f"{source} failed companies: {', '.join(failed) if failed else 'none'}")
    print(f"high_fit count: {len(high_fit_jobs)}")
    print(f"near_fit count: {len(near_fit_jobs)}")
    print(f"low_fit count: {len(low_fit_jobs)}")


if __name__ == "__main__":
    main()
