from __future__ import annotations

import argparse
import json
import logging
from typing import Protocol

from job_fit_agent.collectors.ashby import AshbyCollector
from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.collectors.lever import LeverCollector
from job_fit_agent.config import AppConfig, TargetProfile, load_company_watchlist, load_target_profile
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_top_jobs_by_classification, initialize, upsert_job
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


def _print_digest_rows(section_title: str, rows: list[dict], empty_message: str) -> None:
    print(section_title)
    if not rows:
        print(empty_message)
        return

    for row in rows:
        red_flags = json.loads(row["red_flags"]) if row["red_flags"] else []
        print(f"score: {row['score']}")
        print(f"title: {row['title']}")
        print(f"company: {row['company']}")
        print(f"source: {row['source']}")
        print(f"url: {row['url']}")
        print(f"red_flags: {', '.join(red_flags) if red_flags else 'none'}")
        print("-")


def run_pipeline() -> None:
    target_profile = load_target_profile()
    app_config = AppConfig()
    initialize()

    collectors: dict[str, JobCollector] = {
        "greenhouse": GreenhouseCollector(),
        "ashby": AshbyCollector(),
        "lever": LeverCollector(),
    }

    enabled_collectors = {
        source: collector
        for source, collector in collectors.items()
        if source != "lever" or app_config.enable_lever
    }

    all_ranked: list[tuple[JobPosting, FitScore]] = []
    all_below: list[tuple[JobPosting, FitScore]] = []

    for source, collector in enabled_collectors.items():
        companies = resolve_companies(source=source)
        ranked, below = collect_scored_jobs(collector, target_profile, companies, min_score=45)
        all_ranked.extend(ranked)
        all_below.extend(below)

    all_scored_jobs = all_ranked + all_below

    new_matching: list[tuple[JobPosting, FitScore]] = []

    for job, fit in all_scored_jobs:
        result = upsert_job(job, fit)
        if result.is_new and fit.classification in {"high_fit", "near_fit"}:
            new_matching.append((job, fit))

    high_fit_jobs, near_fit_jobs, _ = group_jobs_by_classification(new_matching)

    if high_fit_jobs:
        print_jobs("High-fit jobs to review", high_fit_jobs, limit=15)

    if near_fit_jobs:
        if high_fit_jobs:
            print()
        print("Near-fit jobs worth reviewing")
        print_jobs(None, near_fit_jobs)

    if not high_fit_jobs and not near_fit_jobs:
        print("No new matching jobs found.")


def print_digest() -> None:
    initialize()
    high_fit_rows = get_top_jobs_by_classification("high_fit", limit=10)
    near_fit_rows = get_top_jobs_by_classification("near_fit", limit=10)

    _print_digest_rows("Saved high-fit jobs", high_fit_rows, "No saved high-fit jobs.")
    print()
    _print_digest_rows("Saved near-fit jobs", near_fit_rows, "No saved near-fit jobs.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="job_fit_agent")
    parser.add_argument("command", nargs="?", choices=["run", "digest"], default="run")
    args = parser.parse_args(argv if argv is not None else [])

    if args.command == "digest":
        print_digest()
    else:
        run_pipeline()


if __name__ == "__main__":
    main()
