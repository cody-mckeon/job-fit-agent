from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Any, Protocol
from urllib.parse import urlparse

from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

from job_fit_agent.collectors.ashby import (
    AshbyCollector,
    extract_ashby_app_data_metadata,
    extract_ashby_hydration_data,
    extract_ashby_json_ld_metadata,
)
from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.collectors.lever import LeverCollector
from job_fit_agent.discovery.providers import StaticCompanyProvider
from job_fit_agent.config import (
    AppConfig,
    DiscoveredCompanies,
    DiscoveredCompany,
    TargetProfile,
    load_company_watchlist,
    load_discovered_companies,
    load_discovery_terms,
    load_discovery_queue,
    load_seed_companies,
    load_notification_config,
    load_target_profile,
    save_company_watchlist,
    save_discovered_companies,
    save_discovery_queue,
)
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.notifications.telegram import send_message, send_message_with_credentials
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

STRONG_FIT_ROLE_TERMS = (
    "product manager",
    "technical product manager",
    "product operations",
    "product engineer",
    "ai product",
    "workflow automation",
    "internal tools",
    "product analytics",
)
ADJACENT_ROLE_TERMS = (
    "technical program manager",
    "program manager, internal systems",
    "growth product",
    "developer tools",
    "solutions",
)
WEAK_ROLE_TERMS = (
    "technical account manager",
    "product marketing",
    "marketing operations",
    "demand generation",
    "lifecycle marketing",
    "risk",
    "grc",
    "finance",
    "customer success",
)
STRONG_OVERLAP_TERMS = ("ai workflow", "agentic", "product systems", "workflow automation", "internal tools", "product analytics")
STANDARD_APPLICATION_FIELDS = {
    "name",
    "first name",
    "last name",
    "email",
    "phone",
    "resume",
    "cover letter",
    "linkedin profile",
    "linkedin",
    "github",
    "github profile",
    "twitter handle",
    "x handle",
    "portfolio",
    "website",
    "personal website",
    "current company",
    "location",
    "what country are you based in?",
}


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
        ("greenhouse", r"^https://(?:boards|job-boards)\.greenhouse\.io/([^/]+)/jobs/([^/?#]+)"),
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
        viability_reasons = ", ".join(fit.viability_reasons) if fit.viability_reasons else "none"
        print(f"viability_reasons: {viability_reasons}")
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
    }
    if app_config.enable_lever:
        collectors["lever"] = LeverCollector()
    return collectors


def _collect_all_scored_jobs(
    enabled_collectors: dict[str, JobCollector],
    target_profile: TargetProfile,
) -> list[tuple[JobPosting, FitScore]]:
    all_ranked: list[tuple[JobPosting, FitScore]] = []
    all_below: list[tuple[JobPosting, FitScore]] = []

    for source, collector in enabled_collectors.items():
        try:
            companies = resolve_companies(source=source)
        except ValueError as exc:
            LOGGER.warning("Skipping source with no configured companies: %s", exc)
            continue
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
    print(f"lever enabled: {app_config.enable_lever}")
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
            if not _is_actionable_real_job_url(job.url):
                print("Telegram notification skipped: invalid or placeholder job URL")
                continue
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

def _is_actionable_digest_row(row: dict) -> bool:
    status = str(safe_row_value(row, "status", "")).lower()
    viability_level = str(safe_row_value(row, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(row, "geographic_eligibility", "review")).lower()

    if status in {"archived", "rejected", "applied"}:
        return False
    if viability_level not in {"apply_now", "review", "stretch"}:
        return False
    if viability_level == "skip":
        return False
    if geographic_eligibility in {"ineligible"}:
        return False
    if geographic_eligibility not in {"eligible", "review"}:
        return False
    if not _is_actionable_real_job_url(str(safe_row_value(row, "url", ""))):
        return False
    title = str(safe_row_value(row, "title", "")).lower()
    score = int(safe_row_value(row, "score", 0) or 0)
    role_text = " ".join(
        str(safe_row_value(row, key, "") or "").lower()
        for key in ("title", "notes", "viability_reasons", "reasons", "red_flags", "role_family")
    )
    has_strong_overlap = any(term in role_text for term in STRONG_OVERLAP_TERMS)
    is_weak_role = any(term in title for term in WEAK_ROLE_TERMS)
    is_strong_role = any(term in title for term in STRONG_FIT_ROLE_TERMS)
    is_adjacent_role = any(term in title for term in ADJACENT_ROLE_TERMS)

    if "product marketing" in title and not any(
        term in role_text for term in ("ai agents", "product analytics", "developer tools", "experimentation", "product systems")
    ):
        return False
    if "technical account manager" in title and not any(
        term in role_text for term in ("technical product", "implementation", "workflow automation", "product systems")
    ):
        return False
    if is_weak_role and score < 45 and not has_strong_overlap:
        return False
    if not (is_strong_role or is_adjacent_role or has_strong_overlap) and is_weak_role:
        return False
    return True


def _is_hard_constraint_skipped_row(row: dict) -> bool:
    viability_level = str(safe_row_value(row, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(row, "geographic_eligibility", "review")).lower()
    return viability_level == "skip" or geographic_eligibility == "ineligible"


def _print_skipped_rows(section_title: str, rows: list[dict], empty_message: str) -> None:
    print(section_title)
    if not rows:
        print(empty_message)
        return
    for row in rows:
        viability_reasons_raw = safe_row_value(row, "viability_reasons", "[]")
        viability_reasons = json.loads(viability_reasons_raw) if viability_reasons_raw else []
        print(f"title: {row['title']}")
        print(f"company: {row['company']}")
        print(f"location_raw: {safe_row_value(row, 'location_raw', safe_row_value(row, 'location', ''))}")
        print(f"geographic_eligibility: {safe_row_value(row, 'geographic_eligibility', 'review')}")
        print(f"viability_reasons: {', '.join(viability_reasons) if viability_reasons else 'none'}")
        print(f"url: {row['url']}")
        print("-")


def print_digest(group_by_status: bool = False, include_skipped: bool = False) -> None:
    initialize()
    high_fit_rows = get_top_jobs_by_classification("high_fit", limit=50)
    near_fit_rows = get_top_jobs_by_classification("near_fit", limit=50)

    actionable_high_fit_rows = [row for row in high_fit_rows if _is_actionable_digest_row(row)]
    actionable_near_fit_rows = [row for row in near_fit_rows if _is_actionable_digest_row(row)]
    skipped_rows = [row for row in high_fit_rows + near_fit_rows if _is_hard_constraint_skipped_row(row)]

    if not group_by_status:
        _print_digest_rows("Actionable high-fit jobs", actionable_high_fit_rows, "No actionable high-fit jobs.")
        print()
        _print_digest_rows("Actionable near-fit jobs", actionable_near_fit_rows, "No actionable near-fit jobs.")
        if include_skipped:
            print()
            _print_skipped_rows("Skipped jobs due to hard constraints", skipped_rows, "No skipped jobs due to hard constraints.")
        return

    print("Saved jobs grouped by status")
    for status in sorted(VALID_STATUSES):
        rows = [row for row in actionable_high_fit_rows + actionable_near_fit_rows if row["status"] == status]
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


def _guess_source_from_url(careers_url: str) -> str:
    lower = careers_url.lower()
    if "ashbyhq.com" in lower:
        return "ashby"
    if "greenhouse.io" in lower:
        return "greenhouse"
    if "lever.co" in lower:
        return "lever"
    return "unknown"


def _company_from_url(careers_url: str) -> str:
    cleaned = careers_url.replace("https://", "").replace("http://", "")
    host = cleaned.split("/")[0]
    parts = [part for part in host.split(".") if part and part not in {"www", "jobs", "careers"}]
    return parts[0] if parts else host


def discover_companies() -> None:
    terms = load_discovery_terms().terms
    seeds = load_seed_companies()
    provider = StaticCompanyProvider(seeds)

    discovered = load_discovered_companies()
    existing_names = {entry.company.lower() for entry in discovered.companies}

    discovered_count = 0
    duplicate_count = 0
    for candidate in provider.discover(terms):
        normalized_name = candidate.company.lower()
        if normalized_name in existing_names:
            duplicate_count += 1
            continue
        discovered.companies.append(candidate)
        existing_names.add(normalized_name)
        discovered_count += 1

    save_discovered_companies(discovered)
    print(f"Loaded {len(terms)} terms")
    print(f"Discovered {discovered_count} companies")
    print(f"Skipped {duplicate_count} duplicates")


def add_discovered_company(company: str, source: str, careers_url: str, reason: str) -> None:
    discovered = load_discovered_companies()
    existing_names = {entry.company.lower() for entry in discovered.companies}
    if company.lower() in existing_names:
        print(f"Discovered company already exists: {company}")
        return

    discovered.companies.append(
        DiscoveredCompany(
            company=company,
            source_guess=source,
            careers_url=careers_url,
            reason_discovered=reason,
            status="new",
        )
    )
    save_discovered_companies(discovered)
    print(f"Added discovered company: {company}")


def approve_company(company: str) -> None:
    discovered = load_discovered_companies()
    target = next((entry for entry in discovered.companies if entry.company.lower() == company.lower()), None)
    if target is None:
        raise ValueError(f"Company '{company}' not found in discovered companies.")

    target.status = "approved"
    source = target.source_guess or _guess_source_from_url(target.careers_url)
    if source in {"ashby", "greenhouse", "lever"}:
        watchlist = load_company_watchlist()
        source_list = getattr(watchlist, source)
        if target.company not in source_list:
            source_list.append(target.company)
            source_list.sort()
            save_company_watchlist(watchlist)
    save_discovered_companies(discovered)
    print(f"Approved company: {target.company}")


def reject_company(company: str) -> None:
    discovered = load_discovered_companies()
    target = next((entry for entry in discovered.companies if entry.company.lower() == company.lower()), None)
    if target is None:
        raise ValueError(f"Company '{company}' not found in discovered companies.")
    target.status = "rejected"
    save_discovered_companies(discovered)
    print(f"Rejected company: {target.company}")


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

    blank_known_limitations_by_company: dict[tuple[str, str], list[str]] = {}
    blank_needs_debug_by_company: dict[tuple[str, str], list[str]] = {}
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
            key = (source, company)
            if source.lower() == "ashby" and company.lower() == "cursor":
                blank_known_limitations_by_company.setdefault(key, []).append(url)
            else:
                blank_needs_debug_by_company.setdefault(key, []).append(url)
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

    print("A. Blank location_raw needing debugging")
    if not blank_needs_debug_by_company:
        print("none")
    for (source, company), urls in sorted(blank_needs_debug_by_company.items(), key=lambda item: len(item[1]), reverse=True):
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
    for urls in blank_needs_debug_by_company.values():
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

    print("\nE. Known source limitations")
    if not blank_known_limitations_by_company:
        print("none")
    for (source, company), urls in sorted(blank_known_limitations_by_company.items(), key=lambda item: len(item[1]), reverse=True):
        print(f"{source}/{company}: {len(urls)}")
        print("  - blank location_raw may be unavailable in source metadata; manual review required")
        for sample in urls[:5]:
            print(f"  - {sample}")


PROFILE_CONTEXT_PATH = Path("profile/profile_context.yaml")
PROFILE_BASE_RESUME_PATH = Path("profile/base_resume.md")
PROFILE_RESUME_RULES_PATH = Path("profile/resume_rules.yaml")
PROFILE_DEFAULT_CONTEXT = {
    "target_positioning": "AI-native product builder/operator",
    "strengths": [
        "workflow automation",
        "product analytics",
        "product systems",
        "digital experience",
        "AI agents",
        "agentic operations",
        "cross-functional execution",
    ],
    "current_projects": [
        "job-fit-agent",
        "OpenClaw workflow automation",
        "Resorts World web analytics/product systems",
        "Pendo/GTM/OneTrust instrumentation",
    ],
    "constraints": [
        "Remote US preferred",
        "Hybrid only in Las Vegas/Henderson/Nevada",
        "Avoid fabricating metrics",
        "Avoid overclaiming software engineering depth",
    ],
}


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "role"


def _load_profile_context() -> dict:
    if not PROFILE_CONTEXT_PATH.exists():
        PROFILE_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_CONTEXT_PATH.write_text(yaml.safe_dump(PROFILE_DEFAULT_CONTEXT, sort_keys=False), encoding="utf-8")
        return PROFILE_DEFAULT_CONTEXT
    with PROFILE_CONTEXT_PATH.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    return {**PROFILE_DEFAULT_CONTEXT, **loaded}


def _load_base_resume() -> str | None:
    if not PROFILE_BASE_RESUME_PATH.exists():
        return None
    return PROFILE_BASE_RESUME_PATH.read_text(encoding="utf-8").strip()


def _load_resume_rules() -> list[str]:
    if not PROFILE_RESUME_RULES_PATH.exists():
        PROFILE_RESUME_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
        PROFILE_RESUME_RULES_PATH.write_text(
            yaml.safe_dump(
                {
                    "rules": [
                        "Do not fabricate employment history",
                        "Do not fabricate metrics",
                        "Preserve company names",
                        "Preserve job titles",
                        "Preserve dates",
                        "Keep claims grounded in base_resume.md",
                        "Use [insert metric if available] for unknown metrics",
                        "Tailor emphasis, not truth",
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    with PROFILE_RESUME_RULES_PATH.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    rules = loaded.get("rules") or []
    return [str(rule) for rule in rules]




def _select_projects_for_role(job_title: str, role_family: str, description: str) -> list[str]:
    text = f"{job_title} {role_family} {description}".lower()
    ai_terms = ("ai", "agent", "llm", "workflow", "automation", "builder")
    analytics_terms = ("analytics", "measurement", "instrumentation", "experimentation", "insights")
    hospitality_terms = ("hospitality", "resort", "digital experience", "guest", "api", "integration")

    if any(term in text for term in ai_terms):
        return [
            "Job Fit Agent",
            "RWLV Priority Governor Agent",
            "Web Product Measurement Framework",
        ]
    if any(term in text for term in analytics_terms):
        return [
            "Web Product Measurement Framework",
            "Resorts World analytics/instrumentation work",
            "Job Fit Agent",
        ]
    if any(term in text for term in hospitality_terms):
        return [
            "Resorts World digital experience work",
            "Hospitality API Integration Exploration",
            "Web Product Measurement Framework",
        ]
    return [
        "Job Fit Agent",
        "RWLV Priority Governor Agent",
        "Web Product Measurement Framework",
    ]


def _project_bullet(project_name: str) -> str:
    bullets = {
        "Job Fit Agent": "Job Fit Agent: role discovery, scoring, and prep workflows that support repeatable application operations.",
        "RWLV Priority Governor Agent": "RWLV Priority Governor Agent: workflow automation for intake prioritization and execution rhythm management.",
        "Web Product Measurement Framework": "Web Product Measurement Framework: event taxonomy, instrumentation standards, and analytics QA for decision-ready reporting.",
        "Hospitality API Integration Exploration": "Hospitality API Integration Exploration: scoped integration discovery for digital experience improvements across hospitality touchpoints.",
        "Resorts World analytics/instrumentation work": "Resorts World analytics/instrumentation work: analytics implementation and instrumentation quality improvements for product measurement.",
        "Resorts World digital experience work": "Resorts World digital experience work: digital journey optimization and product systems support for guest-facing experiences.",
    }
    return bullets.get(project_name, f"{project_name}: relevant project experience.")


def _build_cover_letter(job: dict[str, Any], description: str) -> str:
    raw_company = (safe_row_value(job, "company", "Company") or "Company").strip()
    company_display_names = {
        "turgon-ai": "Turgon AI",
        "gohighlevel": "GoHighLevel",
    }
    company = company_display_names.get(raw_company.lower(), raw_company.title())
    title = (safe_row_value(job, "title", "Role") or "Role").strip()
    jd_line = "Based on the role description, the responsibilities appear to value clear ownership, strong delivery habits, and measurable product outcomes."
    jd_context = "Based on the role description," if description else "Based on the available role details,"
    return f"""Cody McKeon
Las Vegas / Henderson Metro
760-669-9343
mckeonc0827@gmail.com

Dear {company} Hiring Team,

I am applying for the {title} role at {company}. I am interested in the opportunity because it aligns with the way I like to work: building practical systems that improve execution quality and speed. {jd_context} this role appears to value clear ownership, strong delivery habits, and measurable product outcomes. {jd_line}

I fit this role through hands-on work across product systems, workflow automation, analytics instrumentation, AI workflows, digital experience, and cross-functional execution. I work closely with partners across product, engineering, operations, and analytics to define scope, ship improvements, and keep execution grounded in clear signals from users and internal teams.

Relevant project work includes Job Fit Agent for AI, product, and workflow-focused roles, RWLV Priority Governor Agent for agentic operations and workflow automation use cases, and the Web Product Measurement Framework for analytics and product measurement programs. These projects show how I approach repeatable execution, instrumentation quality, and practical AI-assisted workflows without over-claiming scope.

Thank you for your consideration. I would welcome the chance to discuss how my background can support your team in this role.

Sincerely,
Cody McKeon
"""


def prep_application(job_id: int) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        return

    base_resume = _load_base_resume()
    if base_resume is None:
        print("Missing profile/base_resume.md. Add your base resume before running prep-application.")
        return

    profile_context = _load_profile_context()
    resume_rules = _load_resume_rules()
    role_slug = _slugify(job["title"] or "role")
    company_slug = _slugify(job["company"] or "company")
    app_dir = Path("applications") / f"{company_slug}_{role_slug}_{job_id}"
    app_dir.mkdir(parents=True, exist_ok=True)

    reasons = json.loads(job["reasons"] or "[]")
    red_flags = json.loads(job["red_flags"] or "[]")
    viability_reasons = json.loads(job["viability_reasons"] or "[]")
    description = (job["notes"] or "").strip()
    role_family = (safe_row_value(job, "role_family", "") or "").strip()

    decision = "review first"
    if job["classification"] == "high_fit" and job["viability_level"] == "apply_now":
        decision = "apply"
    elif job["classification"] == "near_fit":
        decision = "stretch"
    elif job["classification"] == "low_fit":
        decision = "skip"

    fit_summary = f"""# Fit Summary

- job title: {job['title']}
- company: {job['company']}
- source: {job['source']}
- URL: {job['url']}
- score: {job['score']}
- classification: {job['classification']}
- viability_level: {job['viability_level']}
- location_raw: {job['location_raw'] or job['location']}
- geographic_eligibility: {job['geographic_eligibility']}

## Why this role is interesting
{chr(10).join(f'- {r}' for r in reasons[:5]) or '- Opportunity appears aligned to target role scope.'}

## Why Cody may fit
{chr(10).join(f'- {r}' for r in (reasons[:3] + viability_reasons[:2])) or '- Background in product systems and workflow automation appears relevant.'}

## Why Cody may not fit
{chr(10).join(f'- {f}' for f in (red_flags or ['Potential scope/seniority mismatch requires review.']))}

## Recommended resume angle
- Lead with direct overlap in role family, strongest matching projects, and verified ownership from base_resume.md.

## Recommended application decision
- {decision}
"""

    prioritized_projects = _select_projects_for_role(job["title"], role_family, description)
    top_projects = prioritized_projects[:2]

    resume_strategy = f"""# Resume Strategy

## Recommended headline
- {job['title']} | AI-native Product Builder and Workflow Automation Operator

## Recommended summary angle
- Position Cody as a product-focused builder/operator who uses AI-assisted workflows, product systems, and analytics discipline to execute.

## Top skills to emphasize
- workflow automation
- product analytics
- product systems
- AI agents
- cross-functional execution

## Top projects to include
{chr(10).join(f'- {p}' for p in prioritized_projects)}

## Bullets to strengthen
- Outcomes framed with verified scope and ownership from base_resume.md.
- Cross-functional execution details relevant to {job['company']} and {job['title']}.
- Project detail that maps to {role_family or 'the role family implied by the JD'} without inventing metrics.

## Risks to avoid overclaiming
- Do not claim unverified production adoption.
- Do not add unverified metrics, dates, employers, or certifications.
- Do not imply expert-level software engineering depth beyond documented ownership.
"""

    strengths = profile_context.get("strengths", [])[:3]
    top_strengths = ", ".join(strengths) if strengths else "product systems, workflow automation, and analytics"
    resume_rule_text = "\n".join(f"- {rule}" for rule in resume_rules)

    project_lines = "\n".join(f"- {_project_bullet(name)}" for name in top_projects)

    tailored_resume = f"""# Tailored Resume Draft

## Positioning
AI-native product builder/operator focused on {top_strengths}.

## Tailored Summary
Aligned to {job['company']}'s {job['title']} role by emphasizing directly relevant work from the base resume only.

## Experience Highlights
{base_resume}

## Selected Projects
{project_lines}

## Targeted Value for {job['company']} - {job['title']}
- Build repeatable AI-assisted operating workflows for product and operations teams.
- Improve product instrumentation and analytics quality to support roadmap decisions.
- Create practical agentic workflows that reduce manual process overhead.

## Notes
- Do not add metrics unless validated from source records.
- Keep claims scoped to verified ownership and contribution.

## Resume Rules Applied
{resume_rule_text}
"""

    recruiter_note = f"""Hi, I am interested in the {job['title']} role at {job['company']}.
I focus on AI-native product building with workflow automation, product systems, and product analytics.
In my current work, I lead web analytics and product systems initiatives and build agentic workflows for operational execution.
I also developed projects like job-fit-agent and OpenClaw automation that align with practical product operations outcomes.
If helpful, I can share a concise summary of relevant work and why it maps to this role.
"""

    answer_bank = f"""# Answer Bank

## What motivates you professionally?
I am motivated by shipping practical systems that improve execution quality and speed for teams. I focus on clear ownership, measurable outcomes, and reliable workflows.

## Who are your biggest professional influences?
My influences include operators and product leaders who prioritize clear problem definition, rigorous execution, and measurable user outcomes.

## How did you hear about this role?
I found this role through my targeted job search workflow and reviewed it because the scope appears aligned with my background.

## Why this company?
I am interested in {job['company']} because the role context suggests a team that values product execution, cross-functional collaboration, and practical delivery.

## Why this role?
The {job['title']} scope appears aligned with my background in product systems, workflow automation, and analytics-informed execution.

## Anything else you want us to know?
I value clear communication, practical execution, and responsible use of AI-assisted workflows. I focus on claims I can verify with direct experience.
"""

    risk_flags = f"""# Risk Flags

- location risk: {job['location_raw'] or job['location']} (eligibility: {job['geographic_eligibility']})
- seniority risk: {job['title']} may imply scope beyond verified experience level; review required
- experience requirement risk: {', '.join(red_flags) if red_flags else 'No explicit stored requirement risks; validate against JD details'}
- role mismatch risk: classification={job['classification']}, viability={job['viability_level']}
- compensation/location ambiguity: compensation not stored in current record; confirm during application

- overclaiming risk: avoid adding unverified metrics, employers, dates, certifications, production adoption, or expert engineering claims

## Recommendation
Apply now only if key requirements and location constraints are confirmed; otherwise review first and refine positioning before submitting.
"""
    cover_letter = _build_cover_letter(job, description)

    (app_dir / "fit_summary.md").write_text(fit_summary, encoding="utf-8")
    (app_dir / "resume_strategy.md").write_text(resume_strategy, encoding="utf-8")
    (app_dir / "resume_draft.md").write_text(tailored_resume, encoding="utf-8")
    (app_dir / "submit_resume.md").write_text(_normalize_submit_resume(base_resume), encoding="utf-8")
    (app_dir / "recruiter_note.md").write_text(recruiter_note, encoding="utf-8")
    (app_dir / "answer_bank.md").write_text(answer_bank, encoding="utf-8")
    (app_dir / "risk_flags.md").write_text(risk_flags, encoding="utf-8")
    (app_dir / "cover_letter.md").write_text(cover_letter, encoding="utf-8")

    if job["status"] == "new":
        update_status(job_id, "interested")

    print("Application package created:")
    print(f"{app_dir}/")
    if (app_dir / "application_questions.yaml").exists():
        generate_application_answers(job_id)
    print("Files: fit_summary.md, resume_strategy.md, resume_draft.md, submit_resume.md, recruiter_note.md, answer_bank.md, risk_flags.md, cover_letter.md")


def _application_dir_for_job(job: dict[str, Any], job_id: int) -> Path:
    return Path("applications") / f"{_slugify(job['company'] or 'company')}_{_slugify(job['title'] or 'role')}_{job_id}"


def _extract_application_questions_from_html(html_text: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html_text, "html.parser")
    seen: set[str] = set()
    questions: list[dict[str, Any]] = []
    containers = soup.select("form, [class*='application'], [id*='application'], [class*='ats'], [id*='ats']")
    search_root = containers if containers else [soup]
    for root in search_root:
        for field in root.select("textarea, input, select"):
            field_type = field.name
            if field.name == "input":
                field_type = field.get("type", "input")
            required = field.has_attr("required") or field.get("aria-required") == "true"
            label_text = ""
            field_id = field.get("id")
            if field_id:
                label = root.select_one(f"label[for='{field_id}']")
                if label:
                    label_text = label.get_text(" ", strip=True)
            if not label_text:
                parent_label = field.find_parent("label")
                if parent_label:
                    label_text = parent_label.get_text(" ", strip=True)
            if not label_text:
                continue
            if label_text not in seen:
                seen.add(label_text)
                questions.append({"question": label_text, "source": "url", "field_type": field_type, "required": required})
        for node in root.find_all(string=True):
            text = node.strip()
            if text.endswith("?") and len(text) > 5 and text not in seen:
                seen.add(text)
                questions.append({"question": text, "source": "url", "field_type": "text", "required": False})
    return questions


def _is_standard_field(question: str) -> bool:
    normalized = re.sub(r"\s+", " ", question.strip().lower())
    return normalized in STANDARD_APPLICATION_FIELDS


def _is_answerable_question(question: str, field_type: str) -> bool:
    text = question.strip()
    if not text:
        return False
    normalized = text.lower()
    if _is_standard_field(text):
        return False
    if normalized in {"yes", "no"}:
        return False
    if field_type == "file":
        return False
    if field_type in {"radio", "checkbox", "select"}:
        return False
    if field_type == "textarea":
        return True
    keywords = ("describe", "explain", "why", "how", "tell us", "what motivates", "anything else")
    if any(k in normalized for k in keywords):
        return True
    if text.endswith("?") and len(text) > 10:
        return True
    return False


def _extract_group_prompt(field) -> str:
    parent = field.find_parent(["fieldset", "div"])
    if not parent:
        return ""
    legend = parent.find("legend")
    if legend:
        return legend.get_text(" ", strip=True)
    prompt = parent.find(attrs={"class": re.compile(r"question|prompt|label|title", re.I)})
    return prompt.get_text(" ", strip=True) if prompt else ""


def _extract_application_questions_from_soup(soup: BeautifulSoup, source_url: str, source: str = "browser") -> list[dict[str, Any]]:
    seen: set[str] = set()
    questions: list[dict[str, Any]] = []
    extracted_at = datetime.now(UTC).isoformat()
    for field in soup.select("textarea, input, select"):
        if field.get("type", "").lower() == "hidden":
            continue
        label_text = ""
        field_id = field.get("id")
        if field_id:
            label = soup.select_one(f"label[for='{field_id}']")
            if label:
                label_text = label.get_text(" ", strip=True)
        if not label_text:
            parent_label = field.find_parent("label")
            if parent_label:
                label_text = parent_label.get_text(" ", strip=True)
        if not label_text:
            label_text = _extract_group_prompt(field)
        if field.get("type", "").lower() in {"radio", "checkbox"} and label_text.lower() in {"yes", "no"}:
            label_text = _extract_group_prompt(field)
        if not label_text or label_text in seen:
            continue
        seen.add(label_text)
        input_type = field.get("type", "").lower()
        if field.name == "textarea":
            field_type = "textarea"
        elif field.name == "select":
            field_type = "select"
        elif input_type == "radio":
            field_type = "radio"
        elif input_type == "checkbox":
            field_type = "checkbox"
        elif input_type == "file":
            field_type = "file"
        elif field.name == "input":
            field_type = "input"
        else:
            field_type = "unknown"
        required_attr = field.has_attr("required") or field.get("aria-required") == "true"
        required = True if required_attr else None
        questions.append(
            {
                "question": label_text,
                "source": source,
                "field_type": field_type,
                "required": required,
                "extracted_at": extracted_at,
                "source_url": source_url,
                "is_standard_field": _is_standard_field(label_text),
                "answerable": _is_answerable_question(label_text, field_type),
            }
        )
    return questions




def wait_for_application_form(page) -> str:
    try:
        page.wait_for_selector("textarea, input, select, form, label", state="visible", timeout=15000)
        return "css_form_fields"
    except Exception:
        pass

    try:
        page.get_by_text(re.compile(r"\?", re.I)).first.wait_for(timeout=5000)
        return "question_text"
    except Exception:
        pass

    try:
        page.get_by_text(re.compile(r"required", re.I)).first.wait_for(timeout=5000)
        return "required_text"
    except Exception:
        raise RuntimeError("Application form did not become visible after clicking Apply")

def extract_application_questions_browser(job_id: int, debug: bool = False) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        return
    app_dir = _application_dir_for_job(job, job_id)
    app_dir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Install with: pip install playwright && playwright install chromium")
        return

    stage = "launching_browser"
    page = None
    browser = None
    wait_timeout_ms = 15000

    def _log_stage(stage_name: str) -> None:
        print(f"[browser-extract] stage={stage_name}")

    try:
        with sync_playwright() as p:
            _log_stage(stage)
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            stage = "navigating_to_job_url"
            _log_stage(stage)
            page.goto(job["url"], wait_until="domcontentloaded", timeout=wait_timeout_ms)

            stage = "page_loaded"
            _log_stage(stage)
            page.wait_for_load_state("domcontentloaded", timeout=wait_timeout_ms)

            stage = "apply_button_found"
            _log_stage(stage)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(500)
            try:
                page.wait_for_selector("text=/apply for this job/i", timeout=5000)
            except Exception:
                try:
                    page.wait_for_selector("text=/apply/i", timeout=5000)
                except Exception:
                    pass

            apply_candidates = [
                ("role:button exact apply for this job", page.get_by_role("button", name=re.compile(r"apply for this job", re.I))),
                ("role:button apply", page.get_by_role("button", name=re.compile(r"apply", re.I))),
                ("button filter has_text=apply", page.locator("button").filter(has_text=re.compile(r"apply", re.I))),
                ("link contains /application", page.locator('[href*="application"]')),
            ]
            clicked = False
            for selector_name, locator in apply_candidates:
                count = locator.count()
                print(f"[browser-extract] apply_candidate selector={selector_name}; count={count}")
                if count > 0:
                    print(f"[browser-extract] apply_selector_matched={selector_name}")
                    locator.first.scroll_into_view_if_needed()
                    locator.first.click()
                    clicked = True
                    break
            if not clicked:
                raise RuntimeError("No apply button selector matched")

            stage = "apply_button_clicked"
            _log_stage(stage)

            stage = "form_loaded"
            _log_stage(stage)
            try:
                strategy = wait_for_application_form(page)
            except RuntimeError:
                try:
                    (app_dir / "browser_debug_snapshot.html").write_text(page.content(), encoding="utf-8")
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(app_dir / "browser_debug_screenshot.png"), full_page=True)
                except Exception:
                    pass
                raise
            print(f"[browser-extract] form_wait_strategy={strategy}")

            stage = "questions_extracted"
            _log_stage(stage)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            questions = _extract_application_questions_from_soup(soup, page.url)

            stage = "saving_results"
            _log_stage(stage)
            payload = {"questions": questions}
            (app_dir / "application_questions.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            print(f"Saved {len(questions)} application questions to {app_dir / 'application_questions.yaml'}")
    except Exception as exc:
        print(
            "Browser extraction failed. "
            f"stage={stage}; exception_type={type(exc).__name__}; exception_message={exc}; "
            f"current_url={getattr(page, 'url', 'unavailable')}"
        )
        print("You may need to inspect the application manually.")
        return
    finally:
        if debug and page is not None:
            try:
                (app_dir / "browser_debug_snapshot.html").write_text(page.content(), encoding="utf-8")
            except Exception:
                pass
            try:
                page.screenshot(path=str(app_dir / "browser_debug_screenshot.png"), full_page=True)
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def extract_application_questions(job_id: int) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        return
    app_dir = _application_dir_for_job(job, job_id)
    app_dir.mkdir(parents=True, exist_ok=True)
    response = requests.get(job["url"], timeout=20)
    response.raise_for_status()
    questions = _extract_application_questions_from_html(response.text)
    if not questions:
        print("No application questions found in static HTML. Try manual add-application-question or browser extraction later.")
        return
    payload = {"questions": [{**q, "extracted_at": datetime.now(UTC).isoformat()} for q in questions]}
    (app_dir / "application_questions.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    print(f"Saved {len(questions)} application questions to {app_dir / 'application_questions.yaml'}")


def add_application_question(job_id: int, question: str) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        return
    app_dir = _application_dir_for_job(job, job_id)
    app_dir.mkdir(parents=True, exist_ok=True)
    path = app_dir / "application_questions.yaml"
    data = {"questions": []}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {"questions": []}
    existing = {item.get("question", "").strip().lower() for item in data.get("questions", [])}
    if question.strip().lower() in existing:
        print("Question already exists. Skipping duplicate.")
        return
    data.setdefault("questions", []).append(
        {"question": question.strip(), "source": "manual", "field_type": "text", "required": False, "extracted_at": datetime.now(UTC).isoformat(), "is_standard_field": _is_standard_field(question), "answerable": _is_answerable_question(question, "text")}
    )
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"Added question to {path}")


def generate_application_answers(job_id: int) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if job is None:
        print(f"Job not found: {job_id}")
        return
    app_dir = _application_dir_for_job(job, job_id)
    questions_path = app_dir / "application_questions.yaml"
    if not questions_path.exists():
        print("Missing application_questions.yaml. Run extract-application-questions or add-application-question first.")
        return
    payload = yaml.safe_load(questions_path.read_text(encoding="utf-8")) or {}
    questions = payload.get("questions", [])
    base_resume = (_load_base_resume() or "").strip()
    profile_context = yaml.safe_dump(_load_profile_context(), sort_keys=False)
    answer_bank = (app_dir / "answer_bank.md").read_text(encoding="utf-8") if (app_dir / "answer_bank.md").exists() else ""
    blocks = ["# Application Answers"]
    for item in questions:
        q = item.get("question", "").strip()
        field_type = item.get("field_type", "text")
        if bool(item.get("is_standard_field")) or _is_standard_field(q):
            continue
        if item.get("answerable") is False:
            continue
        if item.get("answerable") is None and not _is_answerable_question(q, field_type):
            continue
        if not q:
            continue
        draft_answer, notes = _build_application_answer(q, str(safe_row_value(job, "company", "")))
        blocks.append(f"\n## {q}\n")
        blocks.append(f"Draft answer: {draft_answer}")
        blocks.append(f"Notes to verify before submitting: {notes}")
    blocks.append("\n## Sources used\n- job data\n- profile/base_resume.md\n- profile/profile_context.yaml\n- answer_bank.md when available")
    _ = (base_resume, profile_context, answer_bank)
    (app_dir / "application_answers.md").write_text("\n".join(blocks) + "\n", encoding="utf-8")
    print(f"Generated {app_dir / 'application_answers.md'}")


def _display_company_name(company: str) -> str:
    company_display_names = {
        "linear": "Linear",
        "stripe": "Stripe",
        "gohighlevel": "GoHighLevel",
        "turgon-ai": "Turgon AI",
    }
    raw = (company or "").strip()
    return company_display_names.get(raw.lower(), raw.title() if raw else "the company")


def _build_application_answer(question: str, company: str) -> tuple[str, str]:
    q = question.lower()
    company_name = _display_company_name(company)
    ai_feature_patterns = [
        "ai-powered product feature",
        "shipped ai",
        "techniques and technologies",
        "outcome quality",
        "product evaluation",
    ]
    if any(pattern in q for pattern in ai_feature_patterns):
        answer = (
            f"A recent AI product feature I shipped (highly relevant to roles at {company_name}) is the Job Fit Agent, a workflow tool I built for my own job-search process to automatically discover openings, "
            "score role fit, and generate tailored application artifacts. The feature combines rule-based evaluation with LLM-assisted drafting: first it normalizes job data "
            "from multiple sources, then applies transparent scoring criteria (title fit, scope, compensation, work model, and AI relevance), and finally generates application materials "
            "such as targeted resumes, cover letters, and draft application responses. I implemented this in Python, using YAML-based profile/config inputs, SQLite for durable job and company state, "
            "and GitHub Actions plus Telegram notifications for scheduled runs and review alerts. For quality, I evaluated outcomes through structured human-in-the-loop review: I spot-checked extracted questions, "
            "verified score rationales against the source posting, and edited generated answers before submitting anything. I also used project-level checks (pytest) to catch regressions in parsing and scoring behavior. "
            "I describe this as shipped as an internal workflow tool rather than a broadly adopted production product."
        )
        notes = (
            "Verify whether you want to describe this as shipped, prototype, or internal workflow tool depending on the application context; confirm which technologies to emphasize (SQLite, GitHub Actions, Telegram) "
            "based on the role; keep claims qualitative unless you can provide real measured outcomes."
        )
        return answer, notes
    answer = (
        f"For this question at {_display_company_name(company)}, I would use concrete examples from three projects: Job Fit Agent (AI-assisted role discovery and application drafting), "
        "RWLV Priority Governor Agent (agentic prioritization workflow), and a Web Product Measurement Framework (instrumentation and product health evaluation). I would tailor the example to the prompt, "
        "explain the user/problem context, the implementation approach, and how outcomes were reviewed with a human-in-the-loop before any external use."
    )
    notes = (
        "Choose the strongest matching project for this specific prompt; confirm scope boundaries and timeline language; avoid numeric impact claims unless you can verify them."
    )
    return answer, notes



RESUME_SUBMIT_HEADER = """# Cody McKeon

Las Vegas / Henderson Metro  
760-669-9343 | mckeonc0827@gmail.com | https://github.com/cody-mckeon  

**Technical Product Manager | AI Workflows | Product Systems | Agentic Operations**
"""


def _ensure_submit_resume_header(markdown_text: str) -> str:
    body = markdown_text.replace("\r\n", "\n")
    body_without_h1 = re.sub(r"^\s*#\s+.+\n+", "", body, count=1, flags=re.MULTILINE)
    body_without_contact = re.sub(r"^\s*Las Vegas / Henderson Metro\s*\n?", "", body_without_h1, count=1, flags=re.MULTILINE)
    body_without_contact = re.sub(r"^\s*760-669-9343\s*\|\s*mckeonc0827@gmail\.com\s*\|\s*https://github\.com/cody-mckeon\s*\n?", "", body_without_contact, count=1, flags=re.MULTILINE)
    body_without_headline = re.sub(r"^\s*\*\*Technical Product Manager \| AI Workflows \| Product Systems \| Agentic Operations\*\*\s*\n?", "", body_without_contact, count=1, flags=re.MULTILINE)
    body_without_headline = body_without_headline.lstrip("\n")
    return f"{RESUME_SUBMIT_HEADER}\n{body_without_headline.lstrip()}" if body_without_headline.strip() else f"{RESUME_SUBMIT_HEADER}\n"


def _sanitize_resume_name_component(value: str) -> str:
    sanitized = re.sub(r"\s+", "_", value.strip())
    sanitized = sanitized.replace("/", "")
    sanitized = re.sub(r"[^A-Za-z0-9_]", "", sanitized)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    return sanitized or "Unknown"


def format_inline_list(items: list[str]) -> str:
    return ", ".join(item.strip() for item in items if item.strip())


def section(title: str, body: str) -> str:
    return f"\n## {title}\n\n{body.strip()}\n"


def _normalize_submit_resume(markdown_text: str) -> str:
    normalized = markdown_text.replace("\r\n", "\n")
    section_order = [
        "Professional Summary",
        "Core Skills",
        "Tools & Platforms",
        "Professional Experience",
        "Projects",
        "Education",
    ]
    for section_name in section_order:
        normalized = re.sub(rf"([^\n])\s*(##\s+{re.escape(section_name)})", r"\1\n\2", normalized)
    lines = normalized.split("\n")

    def _convert_section(section_name: str) -> None:
        heading = f"## {section_name}"
        for idx, line in enumerate(lines):
            if line.strip() != heading:
                continue
            section_start = idx + 1
            while section_start < len(lines) and not lines[section_start].strip():
                section_start += 1
            section_end = section_start
            bullet_values: list[str] = []
            while section_end < len(lines):
                stripped = lines[section_end].strip()
                if stripped.startswith("## "):
                    break
                if stripped:
                    if stripped.startswith("- "):
                        bullet_values.append(stripped[2:].strip())
                    elif stripped.startswith("•"):
                        bullet_values.append(stripped[1:].strip())
                    else:
                        return
                section_end += 1
            if not bullet_values:
                return
            lines[section_start:section_end] = [format_inline_list(bullet_values)]
            return

    _convert_section("Core Skills")
    _convert_section("Tools & Platforms")

    summary_heading = "## Professional Summary"
    for idx, line in enumerate(lines):
        if line.strip() != summary_heading:
            continue
        paragraph_start = idx + 1
        while paragraph_start < len(lines) and not lines[paragraph_start].strip():
            paragraph_start += 1
        paragraph_end = paragraph_start
        paragraphs: list[str] = []
        current_chunk: list[str] = []
        while paragraph_end < len(lines):
            stripped = lines[paragraph_end].strip()
            if stripped.startswith("## "):
                break
            if not stripped:
                if current_chunk:
                    paragraphs.append(" ".join(current_chunk))
                    current_chunk = []
            else:
                current_chunk.append(stripped)
            paragraph_end += 1
        if current_chunk:
            paragraphs.append(" ".join(current_chunk))
        if paragraphs:
            lines[paragraph_start:paragraph_end] = [paragraphs[0]]
        break

    normalized_text = "\n".join(lines).strip() + "\n"

    heading_pattern = re.compile(r"^\s*##\s+(.+?)\s*$")
    section_bodies: dict[str, list[str]] = {name: [] for name in section_order}
    current_section: str | None = None
    for raw_line in normalized_text.splitlines():
        match = heading_pattern.match(raw_line)
        if match:
            heading_name = match.group(1).strip()
            current_section = heading_name if heading_name in section_bodies else None
            continue
        if current_section is not None:
            section_bodies[current_section].append(raw_line)

    normalized_sections: list[str] = []
    for section_name in section_order:
        body = "\n".join(section_bodies[section_name]).strip()
        if body:
            normalized_sections.append(section(section_name, body))

    if normalized_sections:
        return _ensure_submit_resume_header("".join(normalized_sections).lstrip("\n"))
    return _ensure_submit_resume_header(normalized_text)


def export_resume_pdf(job_id: int) -> None:
    initialize()
    job = get_job_by_id(job_id)
    if not job:
        raise ValueError(f"Job not found: {job_id}")
    app_dir = Path("applications") / f"{_slugify(job['company'] or 'company')}_{_slugify(job['title'] or 'role')}_{job_id}"
    resume_path = app_dir / "submit_resume.md"
    if not resume_path.exists():
        print("Missing submit_resume.md. Run prep-application <job_id> first.")
        return

    forbidden_phrases = [
        "Tailored Resume Draft",
        "Resume Rules Applied",
        "Targeted Value",
        "Notes",
        "[insert metric if available]",
    ]
    resume_text = resume_path.read_text(encoding="utf-8")
    found_forbidden = [phrase for phrase in forbidden_phrases if phrase in resume_text]
    if found_forbidden:
        print("submit_resume.md contains forbidden internal content: " + ", ".join(found_forbidden))
        return

    company = _sanitize_resume_name_component(str(job["company"]))
    role = _sanitize_resume_name_component(str(job["title"]))
    output_pdf = app_dir / f"Cody_McKeon_{company}_{role}_Resume.pdf"
    subprocess.run(
        [
            "pandoc",
            str(resume_path),
            "-V",
            "geometry:margin=0.5in",
            "-V",
            "fontsize=10pt",
            "-V",
            "pagestyle=empty",
            "-V",
            "linestretch=1.15",
            "-o",
            str(output_pdf),
        ],
        check=True,
    )
    print(f"Resume PDF exported: {output_pdf}")


def _is_prep_next_application_eligible(job: dict[str, Any]) -> bool:
    status = str(safe_row_value(job, "status", "")).lower()
    classification = str(safe_row_value(job, "classification", "")).lower()
    viability_level = str(safe_row_value(job, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(job, "geographic_eligibility", "review")).lower()
    if status not in {"new", "interested"}:
        return False
    if classification not in {"high_fit", "near_fit"}:
        return False
    if viability_level not in {"apply_now", "review", "stretch"}:
        return False
    if geographic_eligibility not in {"eligible", "review"}:
        return False
    if status in {"applied", "rejected", "archived"}:
        return False
    if viability_level == "skip":
        return False
    if geographic_eligibility == "ineligible":
        return False
    if not _is_actionable_real_job_url(str(safe_row_value(job, "url", ""))):
        return False
    return True


def _is_actionable_real_job_url(url: str) -> bool:
    value = str(url or "").strip()
    if not value:
        return False
    if not (value.startswith("http://") or value.startswith("https://")):
        return False
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False
    if hostname in {"example.com", "localhost", "127.0.0.1", "test.com"}:
        return False
    if "fake" in hostname or "placeholder" in hostname:
        return False
    path = (parsed.path or "").lower()
    if "fake" in path or "placeholder" in path:
        return False
    return True


def _prep_next_application_rank_key(job: dict[str, Any]) -> tuple[int, int, int, int, int]:
    classification_rank = {"high_fit": 0, "near_fit": 1}
    viability_rank = {"apply_now": 0, "review": 1, "stretch": 2}
    geography_rank = {"eligible": 0, "review": 1}
    status_rank = {"new": 0, "interested": 0}
    return (
        classification_rank.get(str(safe_row_value(job, "classification", "")).lower(), 99),
        viability_rank.get(str(safe_row_value(job, "viability_level", "")).lower(), 99),
        -int(safe_row_value(job, "score", 0) or 0),
        geography_rank.get(str(safe_row_value(job, "geographic_eligibility", "")).lower(), 99),
        status_rank.get(str(safe_row_value(job, "status", "")).lower(), 99),
    )


def _get_prep_next_application_candidates() -> list[dict[str, Any]]:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs").fetchall()
    return [dict(row) for row in rows if _is_prep_next_application_eligible(dict(row))]


def _is_actionable_selected_job(job: dict[str, Any]) -> bool:
    status = str(safe_row_value(job, "status", "")).lower()
    classification = str(safe_row_value(job, "classification", "")).lower()
    viability_level = str(safe_row_value(job, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(job, "geographic_eligibility", "review")).lower()
    if classification == "low_fit":
        return False
    if viability_level == "skip":
        return False
    if geographic_eligibility == "ineligible":
        return False
    if status in {"applied", "rejected", "archived"}:
        return False
    if not _is_actionable_real_job_url(str(safe_row_value(job, "url", ""))):
        return False
    return True


def prep_next_application(
    dry_run: bool = False,
    job_id: int | None = None,
    skip_browser: bool = False,
    force: bool = False,
    skip_pdf: bool = False,
) -> dict[str, Any] | None:
    initialize()
    selected_job = None
    if job_id is not None:
        row = get_job_by_id(job_id)
        if row is None:
            print(json.dumps({"error": f"Job not found: {job_id}"}))
            return None
        selected_job = dict(row)
        selected_job_actionable = _is_actionable_selected_job(selected_job)
        if not selected_job_actionable and not force:
            print("Job is not actionable. Use --force to prepare anyway.")
            print(json.dumps({"error": "Job is not actionable. Use --force to prepare anyway."}))
            return None
    else:
        candidates = _get_prep_next_application_candidates()
        if not candidates:
            print(json.dumps({"error": "No actionable jobs found"}))
            return None
        selected_job = sorted(candidates, key=_prep_next_application_rank_key)[0]

    selected_job_id = int(selected_job["id"])
    app_dir = _application_dir_for_job(selected_job, selected_job_id)
    resume_pdf_path = app_dir / (
        f"Cody_McKeon_{_sanitize_resume_name_component(str(selected_job['company']))}_{_sanitize_resume_name_component(str(selected_job['title']))}_Resume.pdf"
    )

    warning = None
    warnings: list[str] = []
    selected_job_actionable = _is_actionable_selected_job(selected_job)
    questions_created = False
    answers_created = False

    pdf_export_status = "skipped" if dry_run else "generated"
    pdf_skipped = bool(dry_run or skip_pdf)
    resume_pdf_path_value: str | None = None if pdf_skipped else str(resume_pdf_path)
    if not dry_run:
        prep_application(selected_job_id)
        if skip_pdf:
            pdf_export_status = "skipped"
        else:
            try:
                export_resume_pdf(selected_job_id)
            except FileNotFoundError as exc:
                if exc.filename == "pandoc":
                    pdf_export_status = "failed"
                    warnings.append("PDF export failed: pandoc not found")
                else:
                    raise
            except subprocess.CalledProcessError:
                pdf_export_status = "failed"
                warnings.append("PDF export failed")
        if skip_browser:
            warning = warning or "browser extraction skipped"
        if not skip_browser:
            before_questions = (app_dir / "application_questions.yaml").exists()
            extract_application_questions_browser(selected_job_id)
            after_questions = (app_dir / "application_questions.yaml").exists()
            questions_created = after_questions
            if not before_questions and not after_questions:
                warning = "application question extraction failed; inspect manually"
            if after_questions:
                generate_application_answers(selected_job_id)
                answers_created = (app_dir / "application_answers.md").exists()
        if str(selected_job.get("status", "")).lower() not in {"applied", "interviewing", "rejected", "archived"}:
            update_status(selected_job_id, "applying")
        try:
            refreshed_job = get_job_by_id(selected_job_id)
        except Exception:
            refreshed_job = None
        if refreshed_job is not None:
            selected_job = dict(refreshed_job)

    summary: dict[str, Any] = {
        "job_id": selected_job_id,
        "company": selected_job.get("company"),
        "title": selected_job.get("title"),
        "url": selected_job.get("url"),
        "score": selected_job.get("score"),
        "classification": selected_job.get("classification"),
        "viability_level": selected_job.get("viability_level"),
        "geographic_eligibility": selected_job.get("geographic_eligibility"),
        "reasons": selected_job.get("reasons") or [],
        "viability_reasons": selected_job.get("viability_reasons") or [],
        "red_flags": selected_job.get("red_flags") or [],
        "application_folder": str(app_dir),
        "submit_resume_path": str(app_dir / "submit_resume.md"),
        "resume_pdf_path": resume_pdf_path_value,
        "pdf_skipped": pdf_skipped,
        "cover_letter_path": str(app_dir / "cover_letter.md"),
        "recruiter_note_path": str(app_dir / "recruiter_note.md"),
        "risk_flags_path": str(app_dir / "risk_flags.md"),
        "next_action": "review package and submit manually" if not dry_run else "review selected job",
        "actionable": selected_job_actionable,
        "skip_browser": skip_browser,
        "pdf_export": pdf_export_status,
    }
    if questions_created:
        summary["application_questions_path"] = str(app_dir / "application_questions.yaml")
    if answers_created:
        summary["application_answers_path"] = str(app_dir / "application_answers.md")
    if warning:
        summary["warning"] = warning
    if warnings:
        summary["warnings"] = warnings
    if force and not selected_job_actionable:
        summary["warning"] = "Prepared despite non-actionable status because --force was used."
    print(json.dumps(summary, indent=2))
    return summary


def _format_prep_next_application_telegram_message(summary: dict[str, Any]) -> str:
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return []

    resume_pdf_line = f"Resume PDF: {summary.get('resume_pdf_path', '')}"
    if summary.get("pdf_skipped"):
        resume_pdf_line = "Resume PDF: skipped in GitHub Actions"
    elif not summary.get("resume_pdf_path"):
        resume_pdf_line = "Resume PDF: unavailable"

    submit_resume_path = str(Path(summary.get("application_folder", "")) / "submit_resume.md")
    lines = [
        "Job Fit Agent: Application Package Ready",
        "",
        "Package",
        f"Company: {summary.get('company', '')}",
        f"Job title: {summary.get('title', '')}",
        f"Score: {summary.get('score', '')}",
        f"Classification: {summary.get('classification', '')}",
        f"Viability level: {summary.get('viability_level', '')}",
        f"Geographic eligibility: {summary.get('geographic_eligibility', '')}",
        f"Job URL: {summary.get('url', '')}",
        "",
        "Paths",
        f"Application folder: {summary.get('application_folder', '')}",
        resume_pdf_line,
        f"Submit resume markdown: {summary.get('submit_resume_path', submit_resume_path)}",
        f"Cover letter: {summary.get('cover_letter_path', '')}",
        f"Recruiter note: {summary.get('recruiter_note_path', '')}",
        f"Risk flags: {summary.get('risk_flags_path', '')}",
    ]
    if summary.get("application_questions_path"):
        lines.append(f"Application questions: {summary.get('application_questions_path')}")
    if summary.get("application_answers_path"):
        lines.append(f"Application answers: {summary.get('application_answers_path')}")

    why_lines: list[str] = []
    fit_reasons = _as_list(summary.get("reasons"))
    viability_reasons = _as_list(summary.get("viability_reasons"))
    red_flags = _as_list(summary.get("red_flags"))
    if fit_reasons:
        why_lines.append(f"Fit: {', '.join(fit_reasons[:2])}")
    if viability_reasons:
        why_lines.append(f"Viability: {', '.join(viability_reasons[:2])}")
    if red_flags:
        why_lines.append(f"Red flags: {', '.join(red_flags[:2])}")
    if why_lines:
        lines.extend(["", "Why this surfaced", *why_lines])

    warnings: list[str] = []
    if summary.get("warning") == "application question extraction failed; inspect manually":
        warnings.append("Browser extraction failed: inspect manually.")
    if summary.get("skip_browser"):
        warnings.append("Browser extraction skipped (--skip-browser).")
    if summary.get("pdf_export") in {"failed", "skipped"}:
        warnings.append(f"PDF export {summary.get('pdf_export')}.")
    if summary.get("pdf_skipped") or summary.get("pdf_export") == "failed":
        warnings.append("PDF export failed or skipped. Review submit_resume.md instead.")
    viability = str(summary.get("viability_level", "")).lower()
    if viability in {"stretch", "review"}:
        warnings.append("Job is stretch/review, not apply_now.")
    geo = str(summary.get("geographic_eligibility", "")).lower()
    if geo == "review":
        warnings.append("Geographic eligibility is review.")
    if warnings:
        lines.extend(["", "Warnings", *warnings])

    lines.extend(["", "GitHub Actions artifact", "Generated files are available in this run's artifact: job-fit-application-package-<run_id>.", "Download: GitHub → Actions → Job Fit Agent → latest run → Artifacts.", "If resume PDF is skipped in GitHub Actions, use submit_resume.md for manual submission."])
    lines.extend(["", "Next action: Review materials manually before submitting."])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    command = args[0] if args else "run"

    if command == "digest":
        print_digest(
            group_by_status="--group-by-status" in args[1:],
            include_skipped="--include-skipped" in args[1:],
        )
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

    if command == "discover-companies":
        discover_companies()
        return

    if command == "add-discovered-company":
        if len(args) < 2:
            print(
                "Usage: python -m job_fit_agent.main add-discovered-company <company> --source <source> --url <careers_url> --reason <reason>"
            )
            return
        company = args[1]
        try:
            source = args[args.index("--source") + 1]
            careers_url = args[args.index("--url") + 1]
            reason = args[args.index("--reason") + 1]
        except (ValueError, IndexError):
            print(
                "Usage: python -m job_fit_agent.main add-discovered-company <company> --source <source> --url <careers_url> --reason <reason>"
            )
            return
        add_discovered_company(company, source, careers_url, reason)
        return

    if command == "approve-company":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main approve-company <company>")
            return
        try:
            approve_company(args[1])
        except ValueError as exc:
            print(str(exc))
        return

    if command == "reject-company":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main reject-company <company>")
            return
        try:
            reject_company(args[1])
        except ValueError as exc:
            print(str(exc))
        return

    if command == "location-audit":
        location_audit()
        return

    if command == "prep-application":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main prep-application <job_id>")
            return
        try:
            prep_application(int(args[1]))
        except ValueError:
            print(f"Job not found: {args[1]}")
        return

    if command == "export-resume-pdf":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main export-resume-pdf <job_id>")
            return
        try:
            export_resume_pdf(int(args[1]))
        except ValueError:
            print(f"Job not found: {args[1]}")
        except subprocess.CalledProcessError as exc:
            print(f"Failed to export PDF: {exc}")
        return

    if command == "extract-application-questions":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main extract-application-questions <job_id>")
            return
        extract_application_questions(int(args[1]))
        return
    if command == "extract-application-questions-browser":
        if len(args) < 2:
            print("Usage: python -m job_fit_agent.main extract-application-questions-browser <job_id> [--debug]")
            return
        debug = "--debug" in args[2:]
        extract_application_questions_browser(int(args[1]), debug=debug)
        return

    if command == "add-application-question":
        if len(args) != 3:
            print('Usage: python -m job_fit_agent.main add-application-question <job_id> "<question>"')
            return
        add_application_question(int(args[1]), args[2])
        return

    if command == "generate-application-answers":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main generate-application-answers <job_id>")
            return
        generate_application_answers(int(args[1]))
        return

    if command == "prep-next-application":
        dry_run = "--dry-run" in args[1:]
        skip_browser = "--skip-browser" in args[1:]
        skip_pdf = "--skip-pdf" in args[1:]
        selected_job_id: int | None = None
        force = "--force" in args[1:]
        if "--job-id" in args[1:]:
            try:
                job_id_index = args.index("--job-id")
                selected_job_id = int(args[job_id_index + 1])
            except (ValueError, IndexError):
                print("Usage: python -m job_fit_agent.main prep-next-application [--dry-run] [--job-id <id>] [--force] [--skip-browser] [--skip-pdf] [--notify-telegram]")
                return
        notify_telegram = "--notify-telegram" in args[1:]
        summary = prep_next_application(
            dry_run=dry_run,
            job_id=selected_job_id,
            skip_browser=skip_browser,
            force=force,
            skip_pdf=skip_pdf,
        )
        if notify_telegram:
            config = load_notification_config().telegram
            if not config.bot_token or not config.chat_id:
                print("Telegram notification skipped: missing credentials")
                return
            if summary is None:
                print("No actionable real job URL found.")
                return
            send_message_with_credentials(
                text=_format_prep_next_application_telegram_message(summary),
                bot_token=config.bot_token,
                chat_id=config.chat_id,
            )
        return

    print("python -m job_fit_agent.main run")
    print("python -m job_fit_agent.main digest")
    print("python -m job_fit_agent.main rescore")
    print("python -m job_fit_agent.main set-status <job_id> <status>")
    print("python -m job_fit_agent.main list-status <status>")
    print('python -m job_fit_agent.main notes <job_id> "<note text>"')
    print("python -m job_fit_agent.main learn-url <job_url>")
    print("python -m job_fit_agent.main promote-discovery <source> <company>")
    print("python -m job_fit_agent.main discover-companies")
    print("python -m job_fit_agent.main add-discovered-company <company> --source <source> --url <careers_url> --reason <reason>")
    print("python -m job_fit_agent.main approve-company <company>")
    print("python -m job_fit_agent.main reject-company <company>")
    print("python -m job_fit_agent.main debug-ashby-url <job_url>")
    print("python -m job_fit_agent.main location-audit")
    print("python -m job_fit_agent.main prep-application <job_id>")
    print("python -m job_fit_agent.main export-resume-pdf <job_id>")
    print("python -m job_fit_agent.main extract-application-questions <job_id>")
    print("python -m job_fit_agent.main extract-application-questions-browser <job_id> [--debug]")
    print('python -m job_fit_agent.main add-application-question <job_id> "<question>"')
    print("python -m job_fit_agent.main generate-application-answers <job_id>")
    print("python -m job_fit_agent.main prep-next-application [--dry-run] [--job-id <id>] [--force] [--skip-browser] [--skip-pdf] [--notify-telegram]")


if __name__ == "__main__":
    main()
