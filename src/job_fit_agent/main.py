from __future__ import annotations

import json
import logging
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Protocol

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

    questions = f"""# Application Questions

No structured application questions were stored for this job posting.

- Suggested answer draft: Prepare concise responses tailored to {job['company']} and {job['title']} using verified examples.
- Verify before submitting: years of relevant experience, location preferences, compensation expectations, and work authorization details.
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
    (app_dir / "application_questions.md").write_text(questions, encoding="utf-8")
    (app_dir / "risk_flags.md").write_text(risk_flags, encoding="utf-8")
    (app_dir / "cover_letter.md").write_text(cover_letter, encoding="utf-8")

    if job["status"] == "new":
        update_status(job_id, "interested")

    print("Application package created:")
    print(f"{app_dir}/")
    print("Files: fit_summary.md, resume_strategy.md, resume_draft.md, submit_resume.md, recruiter_note.md, application_questions.md, risk_flags.md, cover_letter.md")



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


if __name__ == "__main__":
    main()
