from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from typing import Protocol

from pathlib import Path

import requests
from bs4 import BeautifulSoup

from job_fit_agent.collectors.ashby import (
    AshbyCollector,
    extract_ashby_app_data_metadata,
    extract_ashby_hydration_data,
    extract_ashby_json_ld_metadata,
)
from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.collectors.lever import LeverCollector
from job_fit_agent.config import (
    AppConfig,
    TargetProfile,
    load_company_watchlist,
    load_discovery_queue,
    load_notification_config,
    load_target_profile,
    save_company_watchlist,
    save_discovery_queue,
)
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.notifications.telegram import send_message
from job_fit_agent.repository import (
    DB_PATH,
    VALID_STATUSES,
    get_job_by_id,
    get_jobs_by_status,
    get_top_jobs_by_classification,
    initialize,
    update_notes,
    update_status,
    upsert_job,
)
from job_fit_agent.scoring import score_job

LOGGER = logging.getLogger(__name__)


class JobCollector(Protocol):
    def fetch_jobs(self, company: str) -> list[JobPosting]: ...


@dataclass
class ParsedJobUrl:
    source: str
    company: str
    job_id: str
    original_url: str


def parse_job_url(job_url: str) -> ParsedJobUrl:
    patterns = [
        ("ashby", r"^https://jobs\.ashbyhq\.com/([^/]+)/([^/?#]+)"),
        ("greenhouse", r"^https://boards\.greenhouse\.io/([^/]+)/jobs/([^/?#]+)"),
        ("lever", r"^https://jobs\.lever\.co/([^/]+)/([^/?#]+)"),
    ]
    for source, pattern in patterns:
        match = re.match(pattern, job_url)
        if match:
            return ParsedJobUrl(source=source, company=match.group(1), job_id=match.group(2), original_url=job_url)
    raise ValueError(
        "Unsupported job URL. Expected Ashby, Greenhouse, or Lever URL patterns."
    )


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
        print(f"viability_level: {fit.viability_level}")
        print(f"viability_reasons: {", ".join(fit.viability_reasons) if fit.viability_reasons else "none"}")
        print(f"source: {job.source}")
        print(f"title: {job.title}")
        print(f"company: {job.company}")
        print(f"location: {job.location}")
        print(f"normalized_location_type: {job.normalized_location_type}")
        print(f"geographic_eligibility: {job.geographic_eligibility}")
        print(f"workplace_type: {job.workplace_type}")
        print(f"department: {job.department}")
        print(f"team: {job.team}")
        print(f"url: {job.url}")
        print(f"reasons: {', '.join(fit.reasons) if fit.reasons else 'none'}")
        print(f"red_flags: {', '.join(fit.red_flags) if fit.red_flags else 'none'}")
        print("-")


def format_high_fit_notification(job: JobPosting, fit: FitScore) -> str:
    return "\n".join(
        [
            "[HIGH FIT JOB]",
            job.company,
            job.title,
            f"Score: {fit.total_score}",
            f"Source: {job.source}",
            job.url,
        ]
    )


def safe_row_value(row: object, key: str, default: object = None) -> object:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return default


def _print_digest_rows(section_title: str, rows: list[dict], empty_message: str) -> None:
    print(section_title)
    if not rows:
        print(empty_message)
        return

    for row in rows:
        red_flags = json.loads(row["red_flags"]) if row["red_flags"] else []
        print(f"id: {row['id']}")
        print(f"score: {row['score']}")
        print(f"status: {row['status']}")
        print(f"classification: {safe_row_value(row, 'classification', 'unknown')}")
        print(f"viability_level: {safe_row_value(row, 'viability_level', 'review')}")
        print(f"title: {row['title']}")
        print(f"company: {row['company']}")
        print(f"source: {row['source']}")
        print(f"url: {row['url']}")
        print(f"location_raw: {safe_row_value(row, 'location_raw', safe_row_value(row, 'location', ''))}")
        print(f"normalized_location_type: {safe_row_value(row, 'normalized_location_type', '')}")
        print(f"geographic_eligibility: {safe_row_value(row, 'geographic_eligibility', 'review')}")
        viability_reasons_raw = safe_row_value(row, "viability_reasons", "[]")
        viability_reasons = json.loads(viability_reasons_raw) if viability_reasons_raw else []
        print(f"viability_reasons: {', '.join(viability_reasons) if viability_reasons else 'none'}")
        print(f"red_flags: {', '.join(red_flags) if red_flags else 'none'}")
        print("-")




def _build_enabled_collectors(app_config: AppConfig) -> dict[str, JobCollector]:
    collectors: dict[str, JobCollector] = {
        "greenhouse": GreenhouseCollector(),
        "ashby": AshbyCollector(),
        "lever": LeverCollector(),
    }
    return {
        source: collector
        for source, collector in collectors.items()
        if source != "lever" or app_config.enable_lever
    }


def _collect_all_scored_jobs(
    enabled_collectors: dict[str, JobCollector],
    target_profile: TargetProfile,
) -> list[tuple[JobPosting, FitScore]]:
    all_ranked: list[tuple[JobPosting, FitScore]] = []
    all_below: list[tuple[JobPosting, FitScore]] = []

    for source, collector in enabled_collectors.items():
        companies = resolve_companies(source=source)
        ranked, below = collect_scored_jobs(collector, target_profile, companies, min_score=45)
        all_ranked.extend(ranked)
        all_below.extend(below)

    return all_ranked + all_below

def run_pipeline() -> None:
    target_profile = load_target_profile()
    app_config = AppConfig()
    notification_config = load_notification_config().telegram
    initialize()

    has_bot_token = bool(notification_config.bot_token)
    has_chat_id = bool(notification_config.chat_id)
    print(f"notifications enabled: {notification_config.enabled}")
    print(f"telegram bot token present: {has_bot_token}")
    print(f"telegram chat id present: {has_chat_id}")

    enabled_collectors = _build_enabled_collectors(app_config)
    all_scored_jobs = _collect_all_scored_jobs(enabled_collectors, target_profile)

    new_matching: list[tuple[JobPosting, FitScore]] = []
    new_high_fit: list[tuple[JobPosting, FitScore]] = []

    for job, fit in all_scored_jobs:
        result = upsert_job(job, fit)
        if result.is_new and fit.classification in {"high_fit", "near_fit"}:
            new_matching.append((job, fit))
        if result.is_new and fit.classification == "high_fit" and fit.viability_level == "apply_now":
            new_high_fit.append((job, fit))

    high_fit_jobs, near_fit_jobs, _ = group_jobs_by_classification(new_matching)

    if new_high_fit:
        print(f"notification candidates count: {len(new_high_fit)}")

    should_send_notifications = True
    skip_reason = ""
    if not notification_config.enabled:
        should_send_notifications = False
        skip_reason = "disabled"
    elif not has_bot_token:
        should_send_notifications = False
        skip_reason = "missing token"
    elif not has_chat_id:
        should_send_notifications = False
        skip_reason = "missing chat id"
    elif not new_high_fit:
        should_send_notifications = False
        skip_reason = "no new high_fit jobs"

    if should_send_notifications:
        for job, fit in new_high_fit:
            send_message(format_high_fit_notification(job, fit))
    else:
        print(f"Telegram send skipped: {skip_reason}")

    if high_fit_jobs:
        print_jobs("High-fit jobs to review", high_fit_jobs, limit=15)

    if near_fit_jobs:
        if high_fit_jobs:
            print()
        print("Near-fit jobs worth reviewing")
        print_jobs(None, near_fit_jobs)

    if not high_fit_jobs and not near_fit_jobs:
        print("No new matching jobs found.")




def run_rescore() -> None:
    target_profile = load_target_profile()
    app_config = AppConfig()
    initialize()

    enabled_collectors = _build_enabled_collectors(app_config)
    all_scored_jobs = _collect_all_scored_jobs(enabled_collectors, target_profile)

    updated_count = 0
    for job, fit in all_scored_jobs:
        result = upsert_job(job, fit)
        if not result.is_new:
            updated_count += 1

    print("Rescore complete")
    print(f"updated jobs count: {updated_count}")

def print_digest(group_by_status: bool = False) -> None:
    initialize()
    high_fit_rows = get_top_jobs_by_classification("high_fit", limit=10)
    near_fit_rows = get_top_jobs_by_classification("near_fit", limit=10)

    if not group_by_status:
        _print_digest_rows("Saved high-fit jobs", high_fit_rows, "No saved high-fit jobs.")
        print()
        _print_digest_rows("Saved near-fit jobs", near_fit_rows, "No saved near-fit jobs.")
        return

    print("Saved jobs grouped by status")
    for status in sorted(VALID_STATUSES):
        rows = [row for row in high_fit_rows + near_fit_rows if row["status"] == status]
        _print_digest_rows(f"Status: {status}", rows, f"No jobs in status '{status}'.")
        print()


def learn_url(job_url: str) -> None:
    parsed = parse_job_url(job_url)
    target_profile = load_target_profile()
    initialize()
    collectors = _build_enabled_collectors(AppConfig())
    collector = collectors.get(parsed.source)
    if collector is None:
        raise ValueError(f"Source '{parsed.source}' is not enabled.")
    jobs = collector.fetch_jobs(parsed.company)
    scored_jobs = [(job, score_job(job, target_profile)) for job in jobs]
    for job, fit in scored_jobs:
        upsert_job(job, fit)

    queue = load_discovery_queue()
    source_companies = getattr(queue, parsed.source)
    if parsed.company not in source_companies:
        source_companies.append(parsed.company)
        save_discovery_queue(queue)

    high_fit_count = len([1 for _, fit in scored_jobs if fit.classification == "high_fit"])
    near_fit_count = len([1 for _, fit in scored_jobs if fit.classification == "near_fit"])
    print(f"parsed source: {parsed.source}")
    print(f"parsed company: {parsed.company}")
    print(f"parsed job id: {parsed.job_id}")
    print(f"jobs fetched: {len(scored_jobs)}")
    print(f"high_fit jobs found: {high_fit_count}")
    print(f"near_fit jobs found: {near_fit_count}")
    print(f"Discovered company added to discovery queue: {parsed.source}/{parsed.company}")


def debug_ashby_url(job_url: str) -> None:
    response = requests.get(job_url, timeout=15)
    response.raise_for_status()
    html = response.text

    debug_path = Path("debug/ashby_debug.html")
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    has_next_data = '__NEXT_DATA__' in html
    has_hydration_json = bool(extract_ashby_hydration_data(html))
    app_data = extract_ashby_app_data_metadata(html)
    json_ld = extract_ashby_json_ld_metadata(html)

    print(f"title: {title}")
    print(f"has __NEXT_DATA__: {has_next_data}")
    for token in ["Foster City", "Remote USA", "Mexico", "Argentina", "Peru"]:
        print(f"contains '{token}': {token in html}")
    print(f"has hydration JSON metadata: {has_hydration_json}")
    print(f"app_data_found: {bool(app_data)}")
    print(f"json_ld_found: {bool(json_ld)}")

    metadata = extract_ashby_hydration_data(html)
    if metadata:
        print(f"hydration location: {metadata.get('Location', '')}")
        print(f"hydration workplace type: {metadata.get('Location Type', '')}")
        print(f"hydration department: {metadata.get('Department', '')}")
        print(f"hydration team: {metadata.get('Team', '')}")
    merged = {}
    for source in (json_ld, app_data):
        merged.update(source)
    if merged:
        print(f"extracted location_raw: {merged.get('Location', '')}")
        print(f"extracted workplace_type: {merged.get('Location Type', '')}")
        print(f"extracted department: {merged.get('Department', '')}")
        print(f"extracted city: {merged.get('city', '')}")
        print(f"extracted state: {merged.get('state', '')}")
        print(f"extracted country: {merged.get('country', '')}")


def promote_discovery(source: str, company: str) -> None:
    valid_sources = {"ashby", "greenhouse", "lever"}
    if source not in valid_sources:
        raise ValueError(f"Invalid source '{source}'. Expected one of: ashby, greenhouse, lever.")

    queue = load_discovery_queue()
    queued_companies = getattr(queue, source)
    if company not in queued_companies:
        raise ValueError(f"Company '{company}' was not found in discovery queue for source '{source}'.")

    watchlist = load_company_watchlist()
    watchlist_companies = getattr(watchlist, source)
    already_in_watchlist = company in watchlist_companies
    if not already_in_watchlist:
        watchlist_companies.append(company)
        watchlist_companies.sort()
        save_company_watchlist(watchlist)

    queued_companies.remove(company)
    queued_companies.sort()
    save_discovery_queue(queue)

    if already_in_watchlist:
        print(f"{source}/{company} already exists in company watchlist")
        return
    print(f"Promoted {source}/{company} to company watchlist")


REGION_ONLY_TERMS = (
    "europe", "emea", "apac", "latam", "north america", "western europe",
    "japan", "canada", "mexico", "argentina", "peru",
)


def location_audit() -> None:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT source, company, url, location_raw, normalized_location_type, geographic_eligibility, workplace_type
               FROM jobs"""
        ).fetchall()

    blank_by_company: dict[tuple[str, str], list[str]] = {}
    region_by_company: dict[tuple[str, str], list[tuple[str, str]]] = {}
    conflicts: list[sqlite3.Row] = []

    for row in rows:
        source = row["source"] or ""
        company = row["company"] or ""
        url = row["url"] or ""
        raw = (row["location_raw"] or "").strip()
        lower_raw = raw.lower()
        normalized_location_type = (row["normalized_location_type"] or "").lower()
        geographic_eligibility = (row["geographic_eligibility"] or "review").lower()
        workplace_type = (row["workplace_type"] or "").lower()
        combined = f"{lower_raw} {workplace_type}"

        if not raw:
            blank_by_company.setdefault((source, company), []).append(url)
        if any(term in lower_raw for term in REGION_ONLY_TERMS):
            region_by_company.setdefault((source, company), []).append((raw, url))
        if ("remote" in combined and "hybrid" in combined) or ("remote" in combined and "non-us" in combined):
            conflicts.append(row)
        if "remote" in combined and "north america" in combined and geographic_eligibility != "review":
            conflicts.append(row)
        if "remote" in combined and geographic_eligibility == "ineligible" and any(
            term in lower_raw for term in ("us", "usa", "united states")
        ):
            conflicts.append(row)

    print("A. Blank location_raw by company")
    if not blank_by_company:
        print("none")
    for (source, company), urls in sorted(blank_by_company.items(), key=lambda item: len(item[1]), reverse=True):
        print(f"{source}/{company}: {len(urls)}")
        for sample in urls[:5]:
            print(f"  - {sample}")

    print("\nB. Region-only locations by company")
    if not region_by_company:
        print("none")
    for (source, company), samples in sorted(region_by_company.items(), key=lambda item: len(item[1]), reverse=True):
        print(f"{source}/{company}: {len(samples)}")
        for raw, sample_url in samples[:5]:
            print(f"  - {raw} :: {sample_url}")

    print("\nC. Conflicting metadata")
    if not conflicts:
        print("none")
    for row in conflicts[:20]:
        print(
            f"{row['source']}/{row['company']} | {row['location_raw']} | {row['normalized_location_type']} | "
            f"{row['geographic_eligibility']} | {row['url']}"
        )

    print("\nD. Top sample URLs to debug")
    debug_urls = []
    for urls in blank_by_company.values():
        debug_urls.extend(urls[:2])
    for samples in region_by_company.values():
        debug_urls.extend([u for _, u in samples[:2]])
    for row in conflicts[:10]:
        debug_urls.append(row["url"])
    seen = set()
    for url in debug_urls:
        if url and url not in seen:
            seen.add(url)
            print(f"- {url}")


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    command = args[0] if args else "run"

    if command == "digest":
        print_digest(group_by_status="--group-by-status" in args[1:])
        return

    if command == "run":
        run_pipeline()
        return

    if command == "rescore":
        run_rescore()
        return

    if command in {"mark", "set-status"}:
        if len(args) != 3:
            print("Usage: python -m job_fit_agent.main set-status <job_id> <status>")
            print(f"Valid statuses: {', '.join(sorted(VALID_STATUSES))}")
            return
        try:
            job_id = int(args[1])
            update_status(job_id, args[2])
            job = get_job_by_id(job_id)
            print(f"Updated job {job_id} status to {job['status']}.")
        except ValueError as exc:
            print(str(exc))
        return


    if command == "list-status":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main list-status <status>")
            print(f"Valid statuses: {', '.join(sorted(VALID_STATUSES))}")
            return
        try:
            rows = get_jobs_by_status(args[1])
        except ValueError as exc:
            print(str(exc))
            return
        _print_digest_rows(f"Jobs with status '{args[1]}'", rows, f"No jobs with status '{args[1]}'.")
        return

    if command == "notes":
        if len(args) != 3:
            print('Usage: python -m job_fit_agent.main notes <job_id> "<note text>"')
            return
        try:
            job_id = int(args[1])
            update_notes(job_id, args[2])
            print(f"Updated job {job_id} notes.")
        except ValueError as exc:
            print(str(exc))
        return

    if command == "learn-url":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main learn-url <job_url>")
            return
        try:
            learn_url(args[1])
        except ValueError as exc:
            print(str(exc))
        return

    if command == "debug-ashby-url":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main debug-ashby-url <job_url>")
            return
        debug_ashby_url(args[1])
        return

    if command == "promote-discovery":
        if len(args) != 3:
            print("Usage: python -m job_fit_agent.main promote-discovery <source> <company>")
            return
        try:
            promote_discovery(args[1], args[2])
        except ValueError as exc:
            print(str(exc))
        return

    if command == "location-audit":
        location_audit()
        return

    print("python -m job_fit_agent.main run")
    print("python -m job_fit_agent.main digest")
    print("python -m job_fit_agent.main rescore")
    print("python -m job_fit_agent.main set-status <job_id> <status>")
    print("python -m job_fit_agent.main list-status <status>")
    print('python -m job_fit_agent.main notes <job_id> "<note text>"')
    print("python -m job_fit_agent.main learn-url <job_url>")
    print("python -m job_fit_agent.main promote-discovery <source> <company>")
    print("python -m job_fit_agent.main debug-ashby-url <job_url>")
    print("python -m job_fit_agent.main location-audit")


if __name__ == "__main__":
    main()
