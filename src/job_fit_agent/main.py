from __future__ import annotations

import hashlib
import contextlib
import io
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, UTC, date, timedelta
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
    parse_ashby_sidebar_metadata,
)
from job_fit_agent.collectors.greenhouse import GreenhouseCollector
from job_fit_agent.collectors.lever import LeverCollector
from job_fit_agent.application_status import (
    APPLICATION_STATUS_TIMESTAMP_FIELDS,
    EXCLUDED_FROM_AUTO_PREP_APPLICATION_STATUSES,
    build_url_for_stable_key,
    load_application_status,
    load_company_application_blocks,
    normalize_company_key,
    parse_stable_job_key,
    save_application_status,
    save_company_application_blocks,
)
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
from job_fit_agent.notifications.telegram import send_document_with_credentials, send_message, send_message_with_credentials

from job_fit_agent.opportunity_pipeline import (
    SECTION_ORDER,
    build_opportunity_pipeline,
    grouped_pipeline,
    pipeline_review,
    set_company_status,
)
from job_fit_agent.repository import (
    DB_PATH,
    VALID_STATUSES,
    get_job_by_id,
    get_job_by_url,
    get_jobs_by_application_status,
    get_jobs_by_status,
    get_top_jobs_by_classification,
    initialize,
    update_application_tracking,
    update_notes,
    update_status,
    upsert_job,
)
from job_fit_agent.scoring import detect_geography_terms, score_job
from job_fit_agent.telegram_commands import parse_telegram_command
from job_fit_agent.work_opportunities import (
    WORK_SECTION_ORDER,
    add_rfp,
    add_work_opportunity,
    grouped_work_opportunities,
    opportunity_review,
    prep_work_opportunity,
)

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
    "ai automation",
    "ai operations",
    "ai transformation",
    "ai solutions consultant",
    "agentic ai consultant",
    "workflow automation consultant",
    "business process automation",
    "digital automation product manager",
    "internal tools product manager",
    "internal tools pm",
    "ai enablement",
    "servicenow moveworks consultant",
    "power platform solution architect",
    "workato automation engineer",
    "revenue operations automation",
    "revops automation",
    "marketing operations automation",
    "ai solutions engineer",
    "ai solutions architect",
    "ai implementation",
    "ai program manager",
    "automation consultant",
    "automation engineer",
    "business systems",
    "gtm systems",
    "revenue systems",
    "marketing systems",
    "marketing automation",
    "enterprise solutions",
    "technical solutions consultant",
    "solutions engineer, ai",
    "solutions architect, ai",
    "workflow consultant",
    "process automation manager",
    "digital transformation",
    "low-code automation",
    "no-code automation",
    "power platform consultant",
    "power platform developer",
    "servicenow architect",
    "moveworks consultant",
)
ADJACENT_ROLE_TERMS = (
    "technical program manager",
    "tpm",
    "program manager, internal systems",
    "growth product",
    "developer tools",
    "solutions",
    "servicenow consultant",
    "moveworks consultant",
    "power platform architect",
    "microsoft power platform consultant",
    "workato engineer",
    "workato consultant",
    "business systems manager",
    "revenue systems manager",
    "revenue operations systems manager",
    "marketing systems manager",
    "marketing automation manager",
    "product operations manager",
    "product operations lead",
    "internal tools engineer",
    "enterprise solutions architect",
    "enterprise solutions consultant",
    "technical solutions consultant",
    "solutions engineer, ai",
    "solutions architect, ai",
    "digital transformation manager",
    "digital transformation consultant",
    "power platform consultant",
    "power platform developer",
    "servicenow architect",
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
    "project manager",
    "program manager",
    "public sector",
    "security program",
    "sales enablement",
    "support specialist",
    "sales operations manager",
    "recruiter",
    "designer",
)
STRONG_OVERLAP_TERMS = (
    "ai workflow",
    "ai workflows",
    "ai automation",
    "ai operations",
    "ai enablement",
    "ai transformation",
    "ai implementation",
    "ai adoption",
    "generative ai",
    "llm",
    "copilots",
    "ai agents",
    "agentic",
    "product systems",
    "workflow automation",
    "process automation",
    "business process automation",
    "process improvement",
    "internal automation",
    "systems automation",
    "enterprise automation",
    "internal tools",
    "product operations",
    "operations systems",
    "business systems",
    "gtm systems",
    "revops systems",
    "revenue systems",
    "marketing systems",
    "product analytics",
    "integrations",
    "systems design",
    "workflow design",
    "servicenow",
    "moveworks",
    "power platform",
    "workato",
    "zapier",
    "n8n",
    "crm automation",
    "sales operations automation",
    "lifecycle automation",
    "revops automation",
    "marketing operations automation",
)

GEOGRAPHY_REVIEW_WARNING = "Geography requires manual review before applying."
NON_US_GEOGRAPHY_TERMS = (
    "dach", "emea", "apac", "anz", "latam", "europe", "european union", "eu",
    "uk", "london", "germany", "france", "spain", "italy", "netherlands",
    "amsterdam", "korea", "japan", "tokyo", "india", "bengaluru", "bangalore",
    "singapore", "australia", "sydney", "oceania", "middle east", "dubai",
    "brazil", "mexico", "canada", "toronto",
)
EXPLICIT_US_GEOGRAPHY_TERMS = (
    "remote united states", "remote us", "united states remote", "us remote",
    "usa remote", "u.s. remote", "remote-us", "us-remote", "remote usa",
    "located in united states", "located in the united states",
    "based in the united states", "open to candidates based in the united states",
    "based anywhere in the united states", "anywhere in the united states",
    "north america remote", "remote north america", "amer remote", "remote amer",
    "americas remote", "remote americas",
    "las vegas", "henderson", "nevada", "us-based", "us based",
)


def _contains_geography_term(text: str, term: str) -> bool:
    if len(term) <= 3:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))
    return term in text


def _has_explicit_us_geography(text: str) -> bool:
    return any(term in text for term in EXPLICIT_US_GEOGRAPHY_TERMS)


def _has_non_us_geography_signal(job: dict[str, Any]) -> bool:
    text = " ".join(str(safe_row_value(job, key, "") or "").lower() for key in ("title", "location", "location_raw", "geographic_reason", "notes", "viability_reasons", "reasons", "red_flags"))
    if _has_explicit_us_geography(text):
        return False
    return any(_contains_geography_term(text, term) for term in NON_US_GEOGRAPHY_TERMS)


def _geography_warnings_for_job(job: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    text = " ".join(str(safe_row_value(job, key, "") or "").lower() for key in ("title", "location", "location_raw", "geographic_reason", "notes", "viability_reasons", "reasons", "red_flags"))
    geo = str(safe_row_value(job, "geographic_eligibility", "review")).lower()
    if geo in {"review", "ineligible"}:
        warnings.append(GEOGRAPHY_REVIEW_WARNING)
    geographic_reason = str(safe_row_value(job, "geographic_reason", "") or "").strip()
    if geographic_reason:
        warnings.append(geographic_reason)
    elif _has_non_us_geography_signal(job):
        warnings.append("Role has non-US geography signals; confirm US eligibility before applying.")
    return warnings

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


@dataclass
class DirectJobPage:
    html: str
    fetched_with_browser: bool = False


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


def parse_prep_url(job_url: str) -> ParsedJobUrl:
    parsed = parse_job_url(job_url)
    if parsed.source != "ashby":
        raise ValueError("Unsupported job URL for prep-url. Expected Ashby URL pattern: https://jobs.ashbyhq.com/<company>/<job_id>.")
    return parsed


def _fetch_direct_job_page_http(job_url: str) -> DirectJobPage | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; JobFitAgent/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = requests.get(job_url, headers=headers, timeout=20)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.debug("Direct job URL HTTP fetch failed for %s: %s", job_url, exc)
        return None
    html = response.text or ""
    if _direct_job_page_has_extractable_content(html):
        return DirectJobPage(html=html, fetched_with_browser=False)
    LOGGER.debug("Direct job URL HTTP fetch returned insufficient extractable content for %s", job_url)
    return None


def _fetch_direct_job_page_browser(job_url: str, debug: bool = False) -> DirectJobPage | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        LOGGER.debug("Playwright is not installed; cannot browser-fetch direct job URL")
        return None

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(job_url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                LOGGER.debug("Direct job URL browser fetch did not reach networkidle for %s", job_url)
            html = page.content()
            if debug:
                debug_path = Path("debug/prep_url_browser_debug.html")
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(html, encoding="utf-8")
            if _direct_job_page_has_extractable_content(html):
                return DirectJobPage(html=html, fetched_with_browser=True)
    except Exception as exc:
        LOGGER.debug("Direct job URL browser fetch failed for %s: %s", job_url, exc)
        return None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
    return None


def _direct_job_page_has_extractable_content(html: str) -> bool:
    soup = BeautifulSoup(html or "", "html.parser")
    visible_text = soup.get_text(" ", strip=True)
    title = _extract_direct_job_title(soup)
    return bool(title and len(visible_text) >= 80)


def _fetch_direct_job_page(job_url: str, *, skip_browser: bool = False, debug: bool = False) -> DirectJobPage | None:
    page = _fetch_direct_job_page_http(job_url)
    if page is not None:
        if debug:
            debug_path = Path("debug/prep_url_http_debug.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(page.html, encoding="utf-8")
        return page
    if skip_browser:
        return None
    return _fetch_direct_job_page_browser(job_url, debug=debug)


def _extract_json_ld_job_fields(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in scripts:
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict) or str(node.get("@type", "")).lower() != "jobposting":
                continue
            return {
                "title": str(node.get("title") or "").strip(),
                "description": BeautifulSoup(str(node.get("description") or ""), "html.parser").get_text(" ", strip=True),
            }
    return {}


def _extract_app_data_job_fields(html: str) -> dict[str, str]:
    match = re.search(r"window\.__appData\s*=\s*(\{.*?\})\s*;", html, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    posting = payload.get("posting") if isinstance(payload, dict) else None
    if not isinstance(posting, dict):
        return {}
    return {
        "title": str(posting.get("title") or "").strip(),
        "description": BeautifulSoup(str(posting.get("descriptionPlain") or posting.get("description") or ""), "html.parser").get_text(" ", strip=True),
    }


def _extract_direct_job_title(soup: BeautifulSoup) -> str:
    for selector in ('meta[property="og:title"]', 'meta[name="twitter:title"]'):
        node = soup.select_one(selector)
        content = str(node.get("content") or "").strip() if node else ""
        if content:
            return re.sub(r"\s+-\s+Ashby\s*$", "", content).strip()
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
        if title:
            return title
    if soup.title:
        return re.sub(r"\s+-\s+Ashby\s*$", "", soup.title.get_text(" ", strip=True)).strip()
    return ""


def _extract_direct_job_description(soup: BeautifulSoup, html: str) -> str:
    for fields in (_extract_json_ld_job_fields(html), _extract_app_data_job_fields(html)):
        description = fields.get("description", "").strip()
        if description:
            return description

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "nav", "form"]):
        tag.decompose()
    container = soup.find("main") or soup.find("article") or soup.body or soup
    text = container.get_text("\n", strip=True)
    lines: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned or cleaned.lower() in {"department", "location", "location type", "employment type", "apply for this job"}:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        lines.append(cleaned)
    return "\n".join(lines).strip()


def extract_ashby_job_from_direct_page(job_url: str, html: str) -> JobPosting:
    parsed = parse_prep_url(job_url)
    soup = BeautifulSoup(html, "html.parser")
    json_ld_fields = _extract_json_ld_job_fields(html)
    app_data_fields = _extract_app_data_job_fields(html)

    metadata: dict[str, str] = {}
    for source in (
        parse_ashby_sidebar_metadata(html),
        extract_ashby_json_ld_metadata(html),
        extract_ashby_hydration_data(html),
        extract_ashby_app_data_metadata(html),
    ):
        metadata.update({k: v for k, v in source.items() if v})

    title = json_ld_fields.get("title") or app_data_fields.get("title") or _extract_direct_job_title(soup)
    description = _extract_direct_job_description(soup, html)
    location = metadata.get("Location") or ""
    if not location:
        location_parts = [metadata.get("city", ""), metadata.get("state", ""), metadata.get("country", "")]
        location = ", ".join(part for part in location_parts if part)
    workplace_type = metadata.get("Location Type") or ""
    if not workplace_type and "remote" in location.lower():
        workplace_type = "Remote"

    if not title or not description:
        raise ValueError("Could not extract title and description from direct job page.")

    return JobPosting(
        source="ashby",
        company=parsed.company,
        title=title,
        location=location,
        workplace_type=workplace_type,
        department=metadata.get("Department", ""),
        employment_type=metadata.get("Employment Type", ""),
        team=metadata.get("Team", ""),
        url=job_url,
        description=description,
        date_found=datetime.now(UTC),
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
        try:
            row = get_job_by_url(job.url)
        except sqlite3.Error:
            row = None
        if row is not None:
            _enrich_application_status_record_for_job(dict(row))
        if result.is_new and fit.classification in {"high_fit", "near_fit"}:
            new_matching.append((job, fit))
        if (
            result.is_new
            and fit.classification == "high_fit"
            and fit.viability_level == "apply_now"
            and job.geographic_eligibility in {"eligible", "remote_us"}
            and not _is_company_blocked_for_application(dict(row) if row is not None else {"company": job.company})
        ):
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
        try:
            row = get_job_by_url(job.url)
        except sqlite3.Error:
            row = None
        if row is not None:
            _enrich_application_status_record_for_job(dict(row))
        if not result.is_new:
            updated_count += 1

    print("Rescore complete")
    print(f"updated jobs count: {updated_count}")

ACTIONABLE_APPLICATION_STATUS_EXCLUSIONS = {"applied", "skipped", "rejected", "withdrawn", "offer", "blocked"}


def _is_actionable_job(row: dict[str, Any], *, require_real_url: bool = True, require_role_overlap: bool = False) -> bool:
    row = _merge_persistent_status(dict(row))
    classification = str(safe_row_value(row, "classification", "")).lower()
    viability_level = str(safe_row_value(row, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(row, "geographic_eligibility", "review")).lower()
    application_status = str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower()
    status = str(safe_row_value(row, "status", "")).lower()
    if classification not in {"high_fit", "near_fit", "apply_now"}:
        return False
    if viability_level not in {"apply_now", "strong_review"}:
        return False
    if geographic_eligibility != "eligible":
        return False
    if application_status in ACTIONABLE_APPLICATION_STATUS_EXCLUSIONS:
        return False
    if _is_company_blocked_for_application(row):
        return False
    if status in {"applied", "rejected", "archived", "blocked"}:
        return False
    if _has_non_us_geography_signal(row):
        return False
    if require_real_url and not _is_actionable_real_job_url(str(safe_row_value(row, "url", ""))):
        return False
    if require_role_overlap and not _has_prep_eligible_role_overlap(row):
        return False
    return True

def _is_actionable_digest_row(row: dict) -> bool:
    row = _merge_persistent_status(dict(row))
    viability_level = str(safe_row_value(row, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(row, "geographic_eligibility", "review")).lower()
    application_status = str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower()
    status = str(safe_row_value(row, "status", "")).lower()
    if status in {"archived", "rejected", "applied", "blocked"}:
        return False
    if application_status in ACTIONABLE_APPLICATION_STATUS_EXCLUSIONS:
        return False
    if _is_company_blocked_for_application(row):
        return False
    if viability_level not in {"apply_now", "strong_review"}:
        return False
    if geographic_eligibility != "eligible":
        return False
    if _has_non_us_geography_signal(row):
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



def _application_tracking_status(row: dict[str, Any]) -> str:
    return str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower()


def _is_unapplied_high_fit_candidate(row: dict[str, Any]) -> bool:
    row = _merge_persistent_status(dict(row))
    classification = str(safe_row_value(row, "classification", "")).lower()
    if classification not in {"high_fit", "apply_now"}:
        return False
    if _application_tracking_status(row) not in {"", "not_applied"}:
        return False
    if _is_company_blocked_for_application(row):
        return False
    if not _is_actionable_real_job_url(str(safe_row_value(row, "url", ""))):
        return False
    return True


def _unapplied_high_fit_rank_key(row: dict[str, Any]) -> tuple[int, int, int]:
    geography_rank = {"eligible": 0, "remote_us": 0, "review": 1, "ineligible": 2}
    classification_rank = {"apply_now": 0, "high_fit": 1}
    return (
        geography_rank.get(str(safe_row_value(row, "geographic_eligibility", "review")).lower(), 9),
        classification_rank.get(str(safe_row_value(row, "classification", "")).lower(), 9),
        -int(safe_row_value(row, "score", 0) or 0),
    )


def _job_has_application_package(row: dict[str, Any]) -> bool:
    try:
        job_id = int(safe_row_value(row, "id", 0) or 0)
    except (TypeError, ValueError):
        return False
    if job_id <= 0:
        return False
    app_dir = _application_dir_for_job(row, job_id)
    return app_dir.exists() or app_dir.with_suffix(".zip").exists()


def _format_application_tracking_row(row: dict[str, Any]) -> dict[str, Any]:
    geo = str(safe_row_value(row, "geographic_eligibility", "review") or "review").lower()
    warnings = _geography_warnings_for_job(row) if geo in {"review", "ineligible"} else []
    return {
        "job_id": safe_row_value(row, "id"),
        "company": safe_row_value(row, "company", ""),
        "title": safe_row_value(row, "title", ""),
        "score": safe_row_value(row, "score", ""),
        "classification": safe_row_value(row, "classification", ""),
        "viability_level": safe_row_value(row, "viability_level", "review"),
        "geographic_eligibility": safe_row_value(row, "geographic_eligibility", "review"),
        "geographic_reason": safe_row_value(row, "geographic_reason", ""),
        "source": safe_row_value(row, "source", ""),
        "url": safe_row_value(row, "url", ""),
        "warnings": warnings,
        "application_package_exists": _job_has_application_package(row),
    }



def _application_tracking_counts(rows: list[dict[str, Any]] | None = None) -> dict[str, int]:
    if rows is None:
        try:
            rows = _load_all_jobs()
        except Exception:
            rows = []
    rows = [_merge_persistent_status(dict(row)) for row in rows]
    row_keys = set()
    for row in rows:
        try:
            row_keys.add(_stable_job_key_for_job(row))
        except ValueError:
            pass
    status_only = [record for key, record in _application_status_records().items() if key not in row_keys]
    counts = {"unapplied_high_fit_count": sum(1 for row in rows if _is_unapplied_high_fit_candidate(row))}
    for application_status in ("saved", "applied", "interviewing", "rejected", "offer", "withdrawn", "skipped", "blocked"):
        counts[f"{application_status}_count"] = sum(
            1
            for row in rows
            if _application_tracking_status(row) == application_status
            or (application_status == "applied" and str(safe_row_value(row, "status", "")).lower() == "applied")
        ) + sum(1 for row in status_only if str(row.get("application_status", "")).lower() == application_status)
    counts["blocked_count"] += sum(
        1 for record in _company_application_block_records().values()
        if str(record.get("status", "")).lower() == "blocked"
    )
    return counts

def _load_all_jobs() -> list[dict[str, Any]]:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs").fetchall()
    return [_merge_persistent_status(dict(row)) for row in rows]


def get_unapplied_high_fit_rows(
    *,
    eligible_only: bool = False,
    include_review: bool = True,
    include_ineligible: bool = False,
    limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows = sorted([row for row in _load_all_jobs() if _is_unapplied_high_fit_candidate(row)], key=_unapplied_high_fit_rank_key)
    eligible_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    ineligible_rows: list[dict[str, Any]] = []
    for row in rows:
        geo = str(safe_row_value(row, "geographic_eligibility", "review") or "review").lower()
        if geo in {"eligible", "remote_us"} and not _has_non_us_geography_signal(row):
            eligible_rows.append(row)
        elif geo == "review" and not _has_non_us_geography_signal(row):
            if include_review and not eligible_only:
                review_rows.append(row)
        elif geo == "ineligible" or _has_non_us_geography_signal(row):
            if include_ineligible and not eligible_only:
                ineligible_rows.append(row)
    if limit is not None:
        remaining = max(limit, 0)
        eligible_rows = eligible_rows[:remaining]
        remaining -= len(eligible_rows)
        review_rows = review_rows[: max(remaining, 0)]
        remaining -= len(review_rows)
        ineligible_rows = ineligible_rows[: max(remaining, 0)]
    return eligible_rows, review_rows, ineligible_rows


def print_unapplied_high_fit(
    *,
    eligible_only: bool = False,
    include_review: bool = True,
    include_ineligible: bool = False,
    limit: int | None = None,
    as_json: bool = False,
) -> None:
    eligible_rows, review_rows, ineligible_rows = get_unapplied_high_fit_rows(
        eligible_only=eligible_only,
        include_review=include_review,
        include_ineligible=include_ineligible,
        limit=limit,
    )
    if as_json:
        print(json.dumps({
            "eligible": [_format_application_tracking_row(row) for row in eligible_rows],
            "review": [_format_application_tracking_row(row) for row in review_rows],
            "ineligible": [_format_application_tracking_row(row) for row in ineligible_rows],
        }, indent=2))
        return

    _print_application_tracking_rows("Unapplied high-fit jobs", eligible_rows, "No unapplied eligible high-fit jobs.")
    if review_rows:
        print()
        _print_application_tracking_rows("Unapplied high-fit jobs needing geography review", review_rows, "No geography-review high-fit jobs.")
    if ineligible_rows:
        print()
        _print_application_tracking_rows("Unapplied high-fit jobs marked geography ineligible", ineligible_rows, "No geography-ineligible high-fit jobs.")


def _print_application_tracking_rows(section_title: str, rows: list[dict[str, Any]], empty_message: str) -> None:
    print(section_title)
    if not rows:
        print(empty_message)
        return
    for row in rows:
        payload = _format_application_tracking_row(row)
        print(f"id: {payload['job_id']}")
        print(f"company: {payload['company']}")
        print(f"title: {payload['title']}")
        print(f"score: {payload['score']}")
        print(f"classification: {payload['classification']}")
        print(f"viability_level: {payload['viability_level']}")
        print(f"geographic_eligibility: {payload['geographic_eligibility']}")
        if payload.get("geographic_reason"):
            print(f"geographic_reason: {payload['geographic_reason']}")
        print(f"source: {payload['source']}")
        print(f"url: {payload['url']}")
        print(f"application_package_exists: {payload['application_package_exists']}")
        if payload["warnings"]:
            print(f"warning: {'; '.join(payload['warnings'])}")
        print("-")


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _stable_job_key_for_url(job_url: str) -> str:
    parsed = parse_job_url(job_url)
    return f"{parsed.source}:{parsed.company}:{parsed.job_id}"


def _stable_job_key_for_job(job: dict[str, Any]) -> str:
    try:
        return _stable_job_key_for_url(str(safe_row_value(job, "url", "")))
    except ValueError:
        source = _mobile_slug(str(safe_row_value(job, "source", "job") or "job"))
        company = _mobile_slug(str(safe_row_value(job, "company", "company") or "company"))
        job_id = str(safe_row_value(job, "id", "") or _mobile_alias_suffix_for_job(job))
        return f"{source}:{company}:{job_id}"


def _external_job_id_for_job(job: dict[str, Any]) -> str:
    try:
        return parse_job_url(str(safe_row_value(job, "url", ""))).job_id
    except ValueError:
        return str(safe_row_value(job, "id", "") or "")


def _looks_like_stable_job_key(identifier: str) -> bool:
    try:
        parse_stable_job_key(identifier)
    except ValueError:
        return False
    return True


def _application_status_records() -> dict[str, dict[str, Any]]:
    try:
        return load_application_status()
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _company_application_block_records() -> dict[str, dict[str, Any]]:
    try:
        return load_company_application_blocks()
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _parse_company_block_expiration(expires_at: str | None) -> date | None:
    value = str(expires_at or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _company_block_days_remaining(record: dict[str, Any], *, today: date | None = None) -> int | None:
    expires_on = _parse_company_block_expiration(str(record.get("expires_at") or ""))
    if expires_on is None:
        return None
    today = today or datetime.now(UTC).date()
    return (expires_on - today).days


def _is_company_block_record_active(record: dict[str, Any], *, today: date | None = None) -> bool:
    if str(record.get("status", "")).lower() != "blocked":
        return False
    days_remaining = _company_block_days_remaining(record, today=today)
    return days_remaining is None or days_remaining >= 0


def _company_block_for_company(company: str) -> dict[str, Any] | None:
    key = normalize_company_key(company)
    if not key:
        return None
    record = _company_application_block_records().get(key)
    if not record or not _is_company_block_record_active(record):
        return None
    return record


def _company_block_for_job(job: dict[str, Any]) -> dict[str, Any] | None:
    company_block = _company_block_for_company(str(safe_row_value(job, "company", "") or ""))
    if company_block is not None:
        return company_block
    try:
        parsed = parse_job_url(str(safe_row_value(job, "url", "") or ""))
    except ValueError:
        return None
    return _company_block_for_company(parsed.company)


def _is_company_blocked_for_application(job: dict[str, Any]) -> bool:
    return _company_block_for_job(job) is not None


def _expiration_from_days(days: int) -> str:
    if days < 0:
        raise ValueError("--days must be zero or positive.")
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def _validate_expiration_date(expires_at: str | None) -> str | None:
    value = str(expires_at or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("--expires-at must use YYYY-MM-DD.") from exc


def block_company(company: str, reason: str, *, days: int | None = None, expires_at: str | None = None, quiet: bool = False) -> dict[str, Any]:
    company_value = str(company or "").strip()
    reason_value = str(reason or "").strip()
    if not company_value:
        raise ValueError("Company is required.")
    if not reason_value:
        raise ValueError("Company block reason is required.")
    if days is not None and expires_at:
        raise ValueError("Use either --days or --expires-at, not both.")
    expires_at_value = _expiration_from_days(days) if days is not None else _validate_expiration_date(expires_at)
    key = normalize_company_key(company_value)
    timestamp = _utc_timestamp()
    records = load_company_application_blocks()
    existing = records.get(key, {})
    history = existing.get("status_history")
    if not isinstance(history, list):
        history = []
    history_entry = {"status": "blocked", "timestamp": timestamp, "reason": reason_value}
    if expires_at_value:
        history_entry["expires_at"] = expires_at_value
    history.append(history_entry)
    record = dict(existing)
    record.update({
        "company": company_value,
        "status": "blocked",
        "reason": reason_value,
        "blocked_at": timestamp,
        "expires_at": expires_at_value,
        "strategy": "recruiter/manual review",
        "updated_at": timestamp,
        "status_history": history,
    })
    records[key] = record
    save_company_application_blocks(records)
    if not quiet:
        print(f"Blocked company: {company_value}.")
        print(f"reason: {reason_value}")
        if expires_at_value:
            print(f"expires_at: {expires_at_value}")
        print("strategy: recruiter/manual review")
    return record


def unblock_expired_company_blocks(*, quiet: bool = False) -> list[dict[str, Any]]:
    records = load_company_application_blocks()
    timestamp = _utc_timestamp()
    expired: list[dict[str, Any]] = []
    for key, record in records.items():
        if str(record.get("status", "")).lower() != "blocked":
            continue
        days_remaining = _company_block_days_remaining(record)
        if days_remaining is None or days_remaining >= 0:
            continue
        history = record.get("status_history")
        if not isinstance(history, list):
            history = []
        history.append({"status": "expired", "timestamp": timestamp, "reason": "company block expiration elapsed"})
        record["status"] = "expired"
        record["expired_at"] = timestamp
        record["updated_at"] = timestamp
        record["status_history"] = history
        records[key] = record
        expired.append(dict(record))
    if expired:
        save_company_application_blocks(records)
    if not quiet:
        print(f"expired_company_blocks: {len(expired)}")
        for record in expired:
            print(f"company: {record.get('company', '')}")
            print(f"expires_at: {record.get('expires_at', '')}")
            print("status: expired")
            print("-")
    return expired


def _persistent_status_for_stable_key(stable_job_key: str) -> dict[str, Any] | None:
    return _application_status_records().get(stable_job_key)


def _persistent_status_for_job(job: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return _persistent_status_for_stable_key(_stable_job_key_for_job(job))
    except ValueError:
        return None


def _merge_persistent_status(row: dict[str, Any]) -> dict[str, Any]:
    record = _persistent_status_for_job(row)
    if not record:
        return row
    merged = dict(row)
    status = str(record.get("application_status") or "").lower()
    if status:
        merged["application_status"] = status
    for source_key, target_key in (
        ("applied_at", "applied_at"),
        ("interviewing_at", "interviewing_at"),
        ("rejected_at", "rejected_at"),
        ("offer_at", "offer_at"),
        ("withdrawn_at", "withdrawn_at"),
        ("skipped_at", "skipped_at"),
        ("saved_at", "saved_at"),
        ("blocked_at", "blocked_at"),
        ("updated_at", "updated_at"),
        ("note", "application_notes"),
    ):
        if record.get(source_key):
            merged[target_key] = record[source_key]
    return merged


def _row_for_stable_key(stable_job_key: str) -> dict[str, Any] | None:
    for row in _recent_jobs_for_alias_lookup():
        if _job_matches_stable_key(row, stable_job_key):
            return row
    return None


def _stable_key_record_defaults(stable_job_key: str) -> dict[str, Any]:
    source, company, external_job_id = parse_stable_job_key(stable_job_key)
    return {
        "stable_job_key": stable_job_key,
        "company": company,
        "title": None,
        "url": build_url_for_stable_key(source, company, external_job_id),
        "source": source,
        "external_job_id": external_job_id,
        "application_status": "not_applied",
        "applied_at": None,
        "interviewing_at": None,
        "rejected_at": None,
        "offer_at": None,
        "withdrawn_at": None,
        "skipped_at": None,
        "saved_at": None,
        "blocked_at": None,
        "updated_at": None,
        "note": "",
        "status_history": [],
        "identifier_used": stable_job_key,
    }


def _record_for_job(job: dict[str, Any], *, stable_job_key: str | None = None) -> dict[str, Any]:
    key = stable_job_key or _stable_job_key_for_job(job)
    try:
        source, company, external_job_id = parse_stable_job_key(key)
    except ValueError:
        source = str(safe_row_value(job, "source", "") or "")
        company = str(safe_row_value(job, "company", "") or "")
        external_job_id = ""
    return {
        "stable_job_key": key,
        "company": safe_row_value(job, "company", company),
        "title": safe_row_value(job, "title", None),
        "url": safe_row_value(job, "url", None),
        "source": safe_row_value(job, "source", source),
        "external_job_id": external_job_id,
        "score": safe_row_value(job, "score", None),
        "classification": safe_row_value(job, "classification", None),
        "geographic_eligibility": safe_row_value(job, "geographic_eligibility", None),
    }


def _write_application_status_record(
    *,
    stable_job_key: str,
    application_status: str,
    timestamp: str,
    note: str | None,
    identifier_used: str,
    job: dict[str, Any] | None,
) -> dict[str, Any]:
    records = load_application_status()
    existing = records.get(stable_job_key, {})
    record = _stable_key_record_defaults(stable_job_key)
    record.update(existing)
    if job is not None:
        record.update(_record_for_job(job, stable_job_key=stable_job_key))
    record["stable_job_key"] = stable_job_key
    record["application_status"] = application_status
    timestamp_field = APPLICATION_STATUS_TIMESTAMP_FIELDS.get(application_status)
    if timestamp_field:
        record[timestamp_field] = timestamp
    record["note"] = note or ""
    record["updated_at"] = timestamp
    record["identifier_used"] = identifier_used
    history = record.get("status_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "status": application_status,
            "timestamp": timestamp,
            "note": note or "",
            "identifier_used": identifier_used,
        }
    )
    record["status_history"] = history
    records[stable_job_key] = record
    save_application_status(records)
    return record


def _enrich_application_status_record_for_job(job: dict[str, Any]) -> None:
    try:
        stable_job_key = _stable_job_key_for_job(job)
    except ValueError:
        return
    records = load_application_status()
    if stable_job_key not in records:
        return
    record = dict(records[stable_job_key])
    preserved = {
        key: record.get(key)
        for key in (
            "application_status",
            "applied_at",
            "interviewing_at",
            "rejected_at",
            "offer_at",
            "withdrawn_at",
            "skipped_at",
            "saved_at",
            "blocked_at",
            "updated_at",
            "note",
            "status_history",
            "identifier_used",
        )
    }
    record.update(_record_for_job(job, stable_job_key=stable_job_key))
    for key, value in preserved.items():
        record[key] = value
    if record != records[stable_job_key]:
        records[stable_job_key] = record
        save_application_status(records)


def _mobile_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "job"


def _mobile_alias_base_for_job(job: dict[str, Any]) -> str:
    company_slug = _mobile_slug(str(safe_row_value(job, "company", "company") or "company"))
    role_slug = _mobile_slug(str(safe_row_value(job, "title", "role") or "role"))
    return f"{company_slug}-{role_slug}"


def _mobile_alias_suffix_for_job(job: dict[str, Any]) -> str:
    try:
        parsed = parse_job_url(str(safe_row_value(job, "url", "")))
        if parsed.job_id:
            return _mobile_slug(parsed.job_id)[:6] or str(safe_row_value(job, "id", ""))
    except ValueError:
        pass
    stable = str(safe_row_value(job, "url", "")) or str(safe_row_value(job, "id", ""))
    return hashlib.sha1(stable.encode("utf-8")).hexdigest()[:6]


def _recent_jobs_for_alias_lookup() -> list[dict[str, Any]]:
    try:
        return _load_all_jobs()
    except Exception:
        return []


def mobile_command_alias_for_job(job: dict[str, Any], recent_jobs: list[dict[str, Any]] | None = None) -> str:
    base = _mobile_alias_base_for_job(job)
    rows = recent_jobs if recent_jobs is not None else _recent_jobs_for_alias_lookup()
    matching = [row for row in rows if _mobile_alias_base_for_job(row) == base]
    if len(matching) <= 1:
        return base
    return f"{base}-{_mobile_alias_suffix_for_job(job)}"


def _job_matches_stable_key(job: dict[str, Any], identifier: str) -> bool:
    try:
        return _stable_job_key_for_job(job) == identifier
    except ValueError:
        return False


def _resolve_job_identifier(identifier: str) -> dict[str, Any]:
    token = identifier.strip()
    if not token:
        raise ValueError("Job identifier is required.")
    if token.isdigit():
        row = get_job_by_id(int(token))
        if row is None:
            raise ValueError("Job not found.")
        return dict(row)
    if token.startswith(("http://", "https://")):
        row = get_job_by_url(token)
        if row is None:
            raise ValueError("Job not found.")
        return dict(row)

    rows = _recent_jobs_for_alias_lookup()
    stable_matches = [row for row in rows if _job_matches_stable_key(row, token)]
    if len(stable_matches) == 1:
        return stable_matches[0]
    if len(stable_matches) > 1:
        raise ValueError("Stable job key matched multiple jobs; use the job URL or numeric id.")

    alias_matches = [row for row in rows if mobile_command_alias_for_job(row, rows) == token]
    if len(alias_matches) == 1:
        return alias_matches[0]
    if len(alias_matches) > 1:
        raise ValueError("Alias matched multiple jobs. Use the stable fallback command from Telegram.")

    base_matches = [row for row in rows if _mobile_alias_base_for_job(row) == token]
    if len(base_matches) == 1:
        return base_matches[0]
    if len(base_matches) > 1:
        raise ValueError("Alias matched multiple jobs. Use the stable fallback command from Telegram.")

    raise ValueError("Job not found.")


def _resolve_job_for_application_command(job_id: int | None, url: str | None) -> dict[str, Any]:
    if job_id is None and not url:
        raise ValueError("Provide --job-id <id> or --url <job_url>.")
    identifier = str(job_id) if job_id is not None else str(url)
    return _resolve_job_identifier(identifier)


def _mark_application_status(
    application_status: str,
    *,
    job_id: int | None = None,
    url: str | None = None,
    identifier: str | None = None,
    note: str | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    initialize()
    identifier_used = identifier or (str(job_id) if job_id is not None else str(url or ""))
    job: dict[str, Any] | None = None
    warning: str | None = None
    if identifier is not None and _looks_like_stable_job_key(identifier):
        stable_job_key = identifier
        job = _row_for_stable_key(stable_job_key)
        if job is None:
            warning = "Job was not found in local SQLite, but status was recorded by stable key."
    else:
        job = _resolve_job_for_application_command(job_id, url or identifier)
        stable_job_key = _stable_job_key_for_job(job)

    timestamp = _utc_timestamp()
    if job is not None:
        kwargs = {"updated_at": timestamp, "application_notes": note}
        timestamp_field = APPLICATION_STATUS_TIMESTAMP_FIELDS.get(application_status)
        if timestamp_field:
            kwargs[timestamp_field] = timestamp
        update_application_tracking(int(job["id"]), application_status, **kwargs)
        if application_status in {"applied", "interviewing", "rejected"}:
            try:
                update_status(int(job["id"]), application_status)
            except ValueError:
                pass
        job = dict(get_job_by_id(int(job["id"])) or job)

    record = _write_application_status_record(
        stable_job_key=stable_job_key,
        application_status=application_status,
        timestamp=timestamp,
        note=note,
        identifier_used=identifier_used,
        job=job,
    )
    updated = dict(job or record)
    updated["stable_job_key"] = stable_job_key
    updated["warning"] = warning
    if not quiet:
        if warning:
            print(f"Marked {application_status} by stable key: {stable_job_key}. Job details will be enriched when rediscovered.")
            print(f"warning: {warning}")
        elif application_status == "applied":
            print(f"Marked applied: {updated.get('company', '')} {updated.get('title', '')}.")
        elif application_status == "skipped":
            print(f"Marked skipped: {updated.get('company', '')} {updated.get('title', '')}.")
        elif application_status == "saved":
            print(f"Saved for later: {updated.get('company', '')} {updated.get('title', '')}.")
        else:
            print(f"Marked {application_status}: {updated.get('company', '')} {updated.get('title', '')}.")
    return updated


def mark_applied(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("applied", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)


def mark_skipped(job_id: int | None = None, url: str | None = None, reason: str | None = None, *, quiet: bool = False) -> dict[str, Any]:
    return _mark_application_status("skipped", job_id=job_id, url=url, identifier=url if url and _looks_like_stable_job_key(url) else None, note=reason, quiet=quiet)


def mark_saved(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("saved", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)


def mark_blocked(job_id: int | None = None, url: str | None = None, reason: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("blocked", job_id=job_id, url=url, identifier=identifier, note=reason, quiet=quiet)



def mark_rejected(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("rejected", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)


def mark_interviewing(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("interviewing", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)


def mark_offer(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("offer", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)


def mark_withdrawn(job_id: int | None = None, url: str | None = None, note: str | None = None, *, quiet: bool = False, identifier: str | None = None) -> dict[str, Any]:
    if identifier is None and url and _looks_like_stable_job_key(url):
        identifier = url
        url = None
    return _mark_application_status("withdrawn", job_id=job_id, url=url, identifier=identifier, note=note, quiet=quiet)

def _status_result_message(action: str, job: dict[str, Any], note: str = "") -> str:
    if action == "block-company":
        return f"Blocked company: {job.get('company', '')}. Strategy: recruiter/manual review."
    if job.get("warning"):
        return f"Marked {job.get('application_status', action)} by stable key: {job.get('stable_job_key')}. Job details will be enriched when rediscovered."
    company = str(job.get("company", "")).strip()
    title = str(job.get("title", "")).strip()
    label = f"{company} {title}".strip()
    if action == "applied":
        return f"Marked applied: {label}."
    if action == "skip":
        suffix = f" Reason: {note}" if note else ""
        return f"Marked {label} as skipped.{suffix}"
    if action == "save":
        return f"Saved {label} for later."
    if action == "blocked":
        suffix = f" Reason: {note}" if note else ""
        return f"Marked {label} as blocked; recruiter/manual review needed.{suffix}"
    suffix = f" Note: {note}" if note else ""
    return f"Marked {label} as {job.get('application_status', action)}.{suffix}"


def execute_telegram_status_command(command_text: str) -> dict[str, Any]:
    try:
        parsed = parse_telegram_command(command_text)
        if parsed.action == "applied":
            updated = mark_applied(identifier=parsed.job_identifier, quiet=True)
            new_status = "applied"
        elif parsed.action == "skip":
            updated = _mark_application_status("skipped", identifier=parsed.job_identifier, note=parsed.note, quiet=True)
            new_status = "skipped"
        elif parsed.action == "save":
            updated = mark_saved(identifier=parsed.job_identifier, quiet=True)
            new_status = "saved"
        elif parsed.action == "blocked":
            updated = mark_blocked(identifier=parsed.job_identifier, reason=parsed.note, quiet=True)
            new_status = "blocked"
        elif parsed.action == "block-company":
            updated = block_company(parsed.job_identifier, parsed.note, days=parsed.days, expires_at=parsed.expires_at, quiet=True)
            new_status = "blocked"
        elif parsed.action in {"rejected", "interviewing", "offer", "withdrawn"}:
            updated = _mark_application_status(parsed.action, identifier=parsed.job_identifier, note=parsed.note, quiet=True)
            new_status = parsed.action
        else:  # pragma: no cover - parser constrains action values
            raise ValueError("Unsupported command.")
        return {
            "success": True,
            "job_id": updated.get("id"),
            "stable_job_key": updated.get("stable_job_key", ""),
            "company": updated.get("company", ""),
            "title": updated.get("title", ""),
            "new_status": new_status,
            "note": parsed.note,
            "warning": updated.get("warning"),
            "message": _status_result_message(parsed.action, updated, parsed.note),
        }
    except Exception as exc:
        return {"success": False, "message": str(exc) or "Job status command failed."}


def print_applied_jobs(*, limit: int = 50, as_json: bool = False) -> None:
    initialize()
    sqlite_rows = [_merge_persistent_status(dict(row)) for row in get_jobs_by_application_status("applied", limit=limit)]
    rows_by_key: dict[str, dict[str, Any]] = {}
    for row in sqlite_rows:
        rows_by_key[_stable_job_key_for_job(row)] = row
    for key, record in _application_status_records().items():
        if str(record.get("application_status", "")).lower() == "applied" and key not in rows_by_key:
            rows_by_key[key] = dict(record)
    rows = sorted(
        rows_by_key.values(),
        key=lambda row: str(safe_row_value(row, "applied_at", safe_row_value(row, "updated_at", "")) or ""),
        reverse=True,
    )[:limit]
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    print("Applied jobs")
    if not rows:
        print("No applied jobs.")
        return
    for row in rows:
        print(f"company: {safe_row_value(row, 'company', '')}")
        print(f"title: {safe_row_value(row, 'title', '')}")
        print(f"applied_at: {safe_row_value(row, 'applied_at', '')}")
        print(f"score: {safe_row_value(row, 'score', '')}")
        print(f"url: {safe_row_value(row, 'url', '')}")
        print(f"stable_job_key: {safe_row_value(row, 'stable_job_key', '') or _stable_job_key_for_job(row)}")
        print(f"notes: {safe_row_value(row, 'application_notes', safe_row_value(row, 'note', '')) or 'none'}")
        print("-")

def _rows_for_application_status(application_status: str, *, limit: int = 50) -> list[dict[str, Any]]:
    initialize()
    sqlite_rows = [_merge_persistent_status(dict(row)) for row in get_jobs_by_application_status(application_status, limit=limit)]
    rows_by_key: dict[str, dict[str, Any]] = {}
    for row in sqlite_rows:
        rows_by_key[_stable_job_key_for_job(row)] = row
    for key, record in _application_status_records().items():
        if str(record.get("application_status", "")).lower() == application_status and key not in rows_by_key:
            rows_by_key[key] = dict(record)
    timestamp_field = APPLICATION_STATUS_TIMESTAMP_FIELDS.get(application_status, "updated_at")
    return sorted(
        rows_by_key.values(),
        key=lambda row: str(safe_row_value(row, timestamp_field, safe_row_value(row, "updated_at", "")) or ""),
        reverse=True,
    )[:limit]


def _print_application_status_jobs(application_status: str, *, limit: int = 50, as_json: bool = False) -> None:
    rows = _rows_for_application_status(application_status, limit=limit)
    if as_json:
        print(json.dumps(rows, indent=2))
        return
    print(f"{application_status.title()} jobs")
    if not rows:
        print(f"No {application_status} jobs.")
        return
    timestamp_field = APPLICATION_STATUS_TIMESTAMP_FIELDS.get(application_status, "updated_at")
    for row in rows:
        print(f"company: {safe_row_value(row, 'company', '')}")
        print(f"title: {safe_row_value(row, 'title', '')}")
        print(f"application_status: {safe_row_value(row, 'application_status', application_status)}")
        print(f"{timestamp_field}: {safe_row_value(row, timestamp_field, '')}")
        print(f"updated_at: {safe_row_value(row, 'updated_at', '')}")
        print(f"score: {safe_row_value(row, 'score', '')}")
        print(f"url: {safe_row_value(row, 'url', '')}")
        print(f"stable_job_key: {safe_row_value(row, 'stable_job_key', '') or _stable_job_key_for_job(row)}")
        print(f"notes: {safe_row_value(row, 'application_notes', safe_row_value(row, 'note', '')) or 'none'}")
        print("-")


def _suggested_blocked_next_action(row: dict[str, Any]) -> str:
    reason = str(safe_row_value(row, "application_notes", safe_row_value(row, "note", "")) or "").lower()
    if "90" in reason or "limit" in reason or "ashby" in reason:
        return "Recruiter/manual review: ask for application-limit exception or referral-backed review before reapplying."
    return "Relationship strategy: contact recruiter/hiring manager or seek referral before any new application attempt."


def _format_blocked_report_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "company": safe_row_value(row, "company", ""),
        "title": safe_row_value(row, "title", ""),
        "url": safe_row_value(row, "url", ""),
        "reason": safe_row_value(row, "application_notes", safe_row_value(row, "note", "")) or "none",
        "blocked_at": safe_row_value(row, "blocked_at", safe_row_value(row, "updated_at", "")),
        "suggested_next_action": _suggested_blocked_next_action(row),
        "stable_job_key": safe_row_value(row, "stable_job_key", "") or _stable_job_key_for_job(row),
    }


def _format_company_blocked_report_row(record: dict[str, Any]) -> dict[str, Any]:
    days_remaining = _company_block_days_remaining(record)
    effective_status = "blocked" if _is_company_block_record_active(record) else "expired"
    if effective_status == "expired":
        next_action = "Cooldown elapsed: unblock expired block, then review current roles before applying."
    elif days_remaining is None:
        next_action = "Recruiter/manual review: use relationship strategy before any additional application."
    else:
        next_action = "Wait for cooldown expiration or use recruiter/manual review for an exception."
    return {
        "company": record.get("company", ""),
        "title": "Company-level block",
        "url": "",
        "reason": record.get("reason", "none") or "none",
        "blocked_at": record.get("blocked_at", record.get("updated_at", "")),
        "expires_at": record.get("expires_at", "") or "",
        "days_remaining": days_remaining,
        "status": effective_status,
        "suggested_next_action": next_action,
        "stable_job_key": "",
        "strategy": record.get("strategy", "recruiter/manual review"),
    }


def print_blocked_jobs(*, limit: int = 50, as_json: bool = False) -> None:
    try:
        rows = _rows_for_application_status("blocked", limit=limit)
    except sqlite3.Error:
        rows = []
    job_blocks = [_format_blocked_report_row(row) for row in rows]
    company_rows = [_format_company_blocked_report_row(record) for record in _company_application_block_records().values()]
    active_company_blocks = sorted(
        (row for row in company_rows if row.get("status") == "blocked"),
        key=lambda row: str(row.get("blocked_at", "") or ""),
        reverse=True,
    )
    expired_company_blocks = sorted(
        (row for row in company_rows if row.get("status") == "expired"),
        key=lambda row: str(row.get("expires_at", "") or ""),
        reverse=True,
    )
    formatted = (active_company_blocks + job_blocks + expired_company_blocks)[:limit]
    if as_json:
        print(json.dumps(formatted, indent=2))
        return
    print("Blocked, needs relationship strategy")
    if not formatted:
        print("No blocked jobs or companies.")
        return
    for row in formatted:
        print(f"company: {row['company']}")
        print(f"title: {row['title']}")
        if row.get("status"):
            print(f"status: {row['status']}")
        print(f"url: {row['url']}")
        print(f"reason: {row['reason']}")
        print(f"blocked_at: {row['blocked_at']}")
        if row.get("expires_at"):
            print(f"expires_at: {row['expires_at']}")
            print(f"days_remaining: {row['days_remaining']}")
        print(f"suggested_next_action: {row['suggested_next_action']}")
        if row.get("strategy"):
            print(f"strategy: {row['strategy']}")
        print(f"stable_job_key: {row['stable_job_key']}")
        print("-")


def print_rejected_jobs(*, limit: int = 50, as_json: bool = False) -> None:
    _print_application_status_jobs("rejected", limit=limit, as_json=as_json)


def print_pipeline_report(*, limit: int = 50, as_json: bool = False) -> None:
    grouped = {status: _rows_for_application_status(status, limit=limit) for status in ("applied", "interviewing", "offer")}
    if as_json:
        print(json.dumps(grouped, indent=2))
        return
    print("Application pipeline")
    for status in ("applied", "interviewing", "offer"):
        print(f"Status: {status}")
        rows = grouped[status]
        if not rows:
            print(f"No {status} jobs.")
        else:
            for row in rows:
                print(f"company: {safe_row_value(row, 'company', '')}")
                print(f"title: {safe_row_value(row, 'title', '')}")
                print(f"stable_job_key: {safe_row_value(row, 'stable_job_key', '') or _stable_job_key_for_job(row)}")
                print(f"updated_at: {safe_row_value(row, 'updated_at', '')}")
                print(f"notes: {safe_row_value(row, 'application_notes', safe_row_value(row, 'note', '')) or 'none'}")
                print("-")
        print()


def print_outcomes_report(*, limit: int = 50, as_json: bool = False) -> None:
    grouped = {status: _rows_for_application_status(status, limit=limit) for status in ("rejected", "offer", "withdrawn")}
    summary = {status: len(rows) for status, rows in grouped.items()}
    if as_json:
        print(json.dumps({"summary": summary, "jobs": grouped}, indent=2))
        return
    print("Application outcomes")
    for status, count in summary.items():
        print(f"{status}_count: {count}")
    for status in ("rejected", "offer", "withdrawn"):
        print(f"\nStatus: {status}")
        rows = grouped[status]
        if not rows:
            print(f"No {status} jobs.")
        for row in rows:
            print(f"company: {safe_row_value(row, 'company', '')}")
            print(f"title: {safe_row_value(row, 'title', '')}")
            print(f"stable_job_key: {safe_row_value(row, 'stable_job_key', '') or _stable_job_key_for_job(row)}")
            print(f"updated_at: {safe_row_value(row, 'updated_at', '')}")
            print(f"notes: {safe_row_value(row, 'application_notes', safe_row_value(row, 'note', '')) or 'none'}")
            print("-")

def _is_automation_ai_operations_review_row(row: dict) -> bool:
    title = str(safe_row_value(row, "title", "")).lower()
    if "product manager" in title and "internal tools product manager" not in title and "digital automation product manager" not in title:
        return False
    role_text = " ".join(
        str(safe_row_value(row, key, "") or "").lower()
        for key in ("title", "notes", "viability_reasons", "reasons", "red_flags", "role_family")
    )
    role_family = str(safe_row_value(row, "role_family", "")).lower()
    automation_families = {
        "ai_operations",
        "ai_automation",
        "ai_transformation",
        "workflow_automation",
        "business_systems",
        "internal_tools",
        "solutions_architecture",
        "ai_implementation",
        "revops_automation",
        "marketing_ops_automation",
        "product_operations",
    }
    return role_family in automation_families or any(term in role_text for term in STRONG_OVERLAP_TERMS)


def print_digest(group_by_status: bool = False, include_skipped: bool = False) -> None:
    initialize()
    high_fit_rows = [_merge_persistent_status(dict(row)) for row in get_top_jobs_by_classification("high_fit", limit=50)]
    near_fit_rows = [_merge_persistent_status(dict(row)) for row in get_top_jobs_by_classification("near_fit", limit=50)]

    actionable_high_fit_rows = [row for row in high_fit_rows if _is_actionable_digest_row(row)]
    actionable_near_fit_rows = [row for row in near_fit_rows if _is_actionable_digest_row(row)]
    actionable_apply_now_rows = actionable_high_fit_rows + actionable_near_fit_rows
    automation_review_rows = [
        row
        for row in actionable_apply_now_rows
        if _is_automation_ai_operations_review_row(row)
    ]
    skipped_rows = [row for row in high_fit_rows + near_fit_rows if _is_hard_constraint_skipped_row(row)]
    strong_ineligible_rows = [
        row
        for row in high_fit_rows + near_fit_rows
        if str(safe_row_value(row, "geographic_eligibility", "review")).lower() == "ineligible"
        and str(safe_row_value(row, "viability_level", "review")).lower() in {"apply_now", "strong_review"}
        and str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower() not in ACTIONABLE_APPLICATION_STATUS_EXCLUSIONS
    ][:10]
    geography_review_rows = [
        row
        for row in high_fit_rows + near_fit_rows
        if str(safe_row_value(row, "geographic_eligibility", "review")).lower() == "review"
        and str(safe_row_value(row, "status", "")).lower() not in {"archived", "rejected", "applied"}
        and str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower() not in ACTIONABLE_APPLICATION_STATUS_EXCLUSIONS
        and _is_actionable_real_job_url(str(safe_row_value(row, "url", "")))
    ]
    tracking_counts = _application_tracking_counts(list(high_fit_rows) + list(near_fit_rows))
    try:
        blocked_rows = _rows_for_application_status("blocked", limit=10)
    except sqlite3.Error:
        blocked_rows = [
            row for row in high_fit_rows + near_fit_rows
            if str(safe_row_value(row, "application_status", "not_applied") or "not_applied").lower() == "blocked"
        ][:10]

    print("Application tracking summary")
    print(f"unapplied_high_fit_count: {tracking_counts['unapplied_high_fit_count']}")
    print(f"applied_count: {tracking_counts['applied_count']}")
    print(f"interviewing_count: {tracking_counts['interviewing_count']}")
    print(f"rejected_count: {tracking_counts['rejected_count']}")
    print(f"offer_count: {tracking_counts['offer_count']}")
    print(f"withdrawn_count: {tracking_counts['withdrawn_count']}")
    print(f"saved_count: {tracking_counts['saved_count']}")
    print(f"skipped_count: {tracking_counts['skipped_count']}")
    print(f"blocked_count: {tracking_counts['blocked_count']}")
    print()

    if not group_by_status:
        _print_digest_rows("Actionable apply-now roles / Actionable high-fit jobs", actionable_apply_now_rows, "No actionable apply-now roles.")
        print()
        _print_digest_rows("Strong role fit, geography not eligible", strong_ineligible_rows, "No strong role-fit jobs with ineligible geography.")
        print()
        _print_digest_rows("High role fit but geography review / Needs geography review", geography_review_rows, "No jobs needing geography review.")
        print()
        _print_digest_rows("Automation / AI operations roles worth reviewing", automation_review_rows, "No automation / AI operations roles worth reviewing.")
        print()
        print_blocked_jobs(limit=10)
        print()
        _print_digest_rows("Actionable high-fit jobs", actionable_high_fit_rows, "No actionable high-fit jobs.")
        print()
        _print_digest_rows("Actionable near-fit jobs", actionable_near_fit_rows, "No actionable near-fit jobs.")
        if geography_review_rows:
            print()
            _print_digest_rows("High role fit but geography review", geography_review_rows, "No high-fit geography-review jobs.")
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


def prep_url(
    job_url: str,
    *,
    force: bool = False,
    skip_browser: bool = False,
    skip_pdf: bool = False,
    notify_telegram: bool = False,
    debug: bool = False,
) -> dict[str, Any] | None:
    parsed = parse_prep_url(job_url)
    initialize()
    page = _fetch_direct_job_page(job_url, skip_browser=skip_browser, debug=debug)
    if page is None:
        print(json.dumps({"error": "Could not fetch direct job page. Try --debug or run from GitHub Actions."}))
        return None

    try:
        job = extract_ashby_job_from_direct_page(job_url, page.html)
    except ValueError as exc:
        if debug:
            debug_path = Path("debug/prep_url_extract_failed.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(page.html, encoding="utf-8")
        print(json.dumps({"error": f"Could not fetch direct job page. Try --debug or run from GitHub Actions. {exc}"}))
        return None

    target_profile = load_target_profile()
    fit = score_job(job, target_profile)
    upsert_job(job, fit)
    row = get_job_by_url(job_url)
    if row is None:
        print(json.dumps({"error": "Direct job was fetched but could not be loaded from SQLite."}))
        return None
    job_id = int(row["id"])
    if job.description.strip():
        update_notes(job_id, job.description.strip())
        refreshed = get_job_by_url(job_url)
        if refreshed is not None:
            row = refreshed

    row_dict = dict(row)
    if not _is_actionable_selected_job(row_dict) and not force:
        print("Job is not actionable. Use --force to prepare anyway.")
        print(json.dumps({"error": "Job is not actionable. Use --force to prepare anyway."}))
        return None

    prep_output = io.StringIO()
    with contextlib.redirect_stdout(prep_output):
        summary = prep_next_application(
            job_id=job_id,
            skip_browser=skip_browser,
            force=force,
            skip_pdf=skip_pdf,
        )
    if summary is None:
        captured = prep_output.getvalue().strip()
        if captured:
            print(captured)
        return None
    summary["source"] = parsed.source
    summary["external_job_id"] = parsed.job_id
    summary["stable_job_key"] = f"{parsed.source}:{parsed.company}:{parsed.job_id}"
    summary["fetched_with_browser"] = page.fetched_with_browser

    if notify_telegram:
        config = load_notification_config().telegram
        if not config.bot_token or not config.chat_id:
            print("Telegram notification skipped: missing credentials")
        else:
            send_message_with_credentials(
                text=_format_prep_next_application_telegram_message(summary),
                bot_token=config.bot_token,
                chat_id=config.chat_id,
            )
            package_zip_path = str(summary.get("package_zip_path", "")).strip()
            if package_zip_path and bool(summary.get("package_zip_created")):
                try:
                    send_document_with_credentials(
                        file_path=package_zip_path,
                        caption=(
                            f"Application package for {summary.get('title', '')}. "
                            "Review manually before submitting."
                        ),
                        bot_token=config.bot_token,
                        chat_id=config.chat_id,
                    )
                except Exception:
                    print("Telegram package upload failed")

    print(json.dumps(summary, indent=2))
    return summary


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
        rows = conn.execute("SELECT * FROM jobs").fetchall()

    def _audit_value(row: sqlite3.Row, key: str, default: str = "") -> str:
        return str(row[key] if key in row.keys() and row[key] is not None else default)

    blank_known_limitations_by_company: dict[tuple[str, str], list[str]] = {}
    blank_needs_debug_by_company: dict[tuple[str, str], list[str]] = {}
    region_by_company: dict[tuple[str, str], list[tuple[str, str]]] = {}
    conflicts: list[sqlite3.Row] = []

    for row in rows:
        source = _row_text(row, "source")
        company = _row_text(row, "company")
        url = _row_text(row, "url")
        raw = _row_text(row, "location_raw").strip()
        lower_raw = raw.lower()
        normalized_location_type = _row_text(row, "normalized_location_type").lower()
        geographic_eligibility = (_row_text(row, "geographic_eligibility", "review") or "review").lower()
        workplace_type = _row_text(row, "workplace_type").lower()
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

    def _debug_geography_line(row: sqlite3.Row) -> str:
        detected_terms = ", ".join(
            detect_geography_terms(
                _row_text(row, "title"),
                _row_text(row, "location_raw"),
                _row_text(row, "workplace_type"),
            )
        ) or "none"
        detected_location_type = _row_text(row, "normalized_location_type") or "unknown"
        structured_location_type = _row_text(row, "workplace_type") or "unknown"
        return (
            f"title: {_audit_value(row, 'title')} | source: {_audit_value(row, 'source')} | "
            f"location: {_audit_value(row, 'location_raw')} | location_type: {structured_location_type} | "
            f"detected_location_terms: {detected_terms} | detected_location_type: {detected_location_type} | "
            f"geographic_eligibility: {_audit_value(row, 'geographic_eligibility') or 'review'} | "
            f"reason: {_audit_value(row, 'geographic_reason')}"
        )

    print("\nC. Conflicting metadata")
    if not conflicts:
        print("none")
    for row in conflicts[:20]:
        print(f"{_debug_geography_line(row)} | url: {_audit_value(row, 'url')}")

    print("\nD. Geography decision sample")
    for row in rows[:20]:
        print(_debug_geography_line(row))

    print("\nD2. Top sample URLs to debug")
    debug_urls = []
    for urls in blank_needs_debug_by_company.values():
        debug_urls.extend(urls[:2])
    for samples in region_by_company.values():
        debug_urls.extend([u for _, u in samples[:2]])
    for row in conflicts[:10]:
        debug_urls.append(_row_text(row, "url"))
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


def _row_text(row: sqlite3.Row, key: str, default: str = "") -> str:
    return str(safe_row_value(row, key, default) or "")


def _json_list_from_row(row: sqlite3.Row, key: str) -> list[str]:
    raw = safe_row_value(row, key, "")
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return [str(raw)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def _non_us_terms_in_text(text: str) -> list[str]:
    detected: list[str] = []
    lowered = (text or "").lower()
    for term in NON_US_GEOGRAPHY_TERMS:
        if _contains_geography_term(lowered, term):
            label = term.upper() if term in {"dach", "emea", "apac", "anz", "latam", "eu", "uk"} else term.title()
            if label not in detected:
                detected.append(label)
    return detected


def debug_geography(job_id: int) -> None:
    initialize()
    row = get_job_by_id(job_id)
    if row is None:
        print(json.dumps({"error": f"Job not found: {job_id}"}, indent=2))
        return

    job = JobPosting(
        source=_row_text(row, "source"),
        company=_row_text(row, "company"),
        title=_row_text(row, "title"),
        location=_row_text(row, "location"),
        location_raw=_row_text(row, "location_raw"),
        normalized_country=_row_text(row, "normalized_country"),
        normalized_state=_row_text(row, "normalized_state"),
        normalized_city=_row_text(row, "normalized_city"),
        normalized_location_type=_row_text(row, "normalized_location_type"),
        geographic_eligibility=_row_text(row, "geographic_eligibility", "review") or "review",
        geographic_reason=_row_text(row, "geographic_reason"),
        workplace_type=_row_text(row, "workplace_type"),
        department=_row_text(row, "department"),
        employment_type=_row_text(row, "employment_type"),
        team=_row_text(row, "team"),
        url=_row_text(row, "url"),
        description="",
    )
    fit = score_job(job, load_target_profile())

    structured_text = " ".join(
        [
            job.location,
            job.location_raw,
            job.workplace_type,
            job.normalized_country,
            job.normalized_state,
            job.normalized_city,
            job.normalized_location_type,
        ]
    )
    title_text = job.title
    noisy_parts: list[str] = []
    for key in ("reasons", "red_flags", "viability_reasons"):
        noisy_parts.extend(_json_list_from_row(row, key))
    noisy_parts.extend(_row_text(row, key) for key in ("geographic_reason", "notes", "department", "employment_type", "team"))
    noisy_text = " ".join(noisy_parts)
    detected_international_terms = _non_us_terms_in_text(structured_text)
    ignored_noisy_terms = [
        term
        for term in _non_us_terms_in_text(f"{title_text} {noisy_text}")
        if term not in detected_international_terms
    ]
    detected_location_terms = detect_geography_terms(structured_text)
    detected_title_terms = [term for term in detect_geography_terms(title_text) if term not in detected_location_terms]

    print(json.dumps({
        "id": job_id,
        "title": job.title,
        "company": job.company,
        "source": job.source,
        "url": job.url,
        "location": job.location,
        "location_raw": job.location_raw,
        "normalized_country": job.normalized_country,
        "normalized_state": job.normalized_state,
        "normalized_city": job.normalized_city,
        "normalized_location_type": job.normalized_location_type,
        "workplace_type": job.workplace_type,
        "detected_terms": {
            "structured_location": detected_location_terms,
            "title": detected_title_terms,
            "structured_international": detected_international_terms,
            "ignored_noisy": ignored_noisy_terms,
        },
        "detected_location_terms": detected_location_terms,
        "detected_international_terms": detected_international_terms,
        "ignored_noisy_terms": ignored_noisy_terms,
        "geographic_eligibility": job.geographic_eligibility,
        "final_geographic_eligibility": job.geographic_eligibility,
        "geographic_reason": job.geographic_reason,
        "red_flags": fit.red_flags,
        "viability_reasons": fit.viability_reasons,
    }, indent=2))


PROFILE_CONTEXT_PATH = Path("profile/profile_context.yaml")
PROFILE_BASE_RESUME_PATH = Path("profile/base_resume.md")
PROFILE_RESUME_RULES_PATH = Path("profile/resume_rules.yaml")
PROFILE_DEFAULT_CONTEXT = {
    "target_positioning": "technical product builder focused on AI-enabled workflow systems",
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




def _role_text(job_title: str, role_family: str, description: str) -> str:
    return f"{job_title} {role_family} {description}".lower()


def _matches_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


SOLUTIONS_TRANSFORMATION_TERMS = (
    "enterprise solutions engineer",
    "solutions engineer",
    "solution engineer",
    "enterprise solutions consultant",
    "ai solutions consultant",
    "ai solutions engineer",
    "forward deployed engineer",
    "forward-deployed engineer",
    "forward deployed",
    "ai transformation",
    "ai operations",
    "workflow automation",
    "internal tools",
    "implementation",
    "technical discovery",
    "customer-facing",
)


def _is_solutions_or_transformation_role(job_title: str, role_family: str, description: str) -> bool:
    text = _role_text(job_title, role_family, description)
    return _matches_any(text, SOLUTIONS_TRANSFORMATION_TERMS)


def _is_product_management_role(job_title: str, role_family: str, description: str) -> bool:
    text = _role_text(job_title, role_family, description)
    return _matches_any(text, ("technical product manager", "product manager", "ai product manager", "product management", "product lead"))


def _is_analytics_product_systems_role(job_title: str, role_family: str, description: str) -> bool:
    text = _role_text(job_title, role_family, description)
    return _matches_any(
        text,
        (
            "analytics",
            "product systems",
            "measurement",
            "instrumentation",
            "experimentation",
            "insights",
            "event tracking",
            "product operations",
        ),
    )


def _select_projects_for_role(job_title: str, role_family: str, description: str) -> list[str]:
    text = _role_text(job_title, role_family, description)
    title_family_text = _role_text(job_title, role_family, "")
    hospitality_terms = ("hospitality", "resort", "digital experience", "guest", "api", "integration")

    if _matches_any(title_family_text, SOLUTIONS_TRANSFORMATION_TERMS):
        return [
            "AI Product Design Operating System",
            "Job Fit Agent",
            "RWLV Priority Governor Agent",
        ]
    if _is_analytics_product_systems_role(job_title, role_family, ""):
        return [
            "RWLV Priority Governor Agent",
            "AI Product Design Operating System",
            "Job Fit Agent",
        ]
    if _is_product_management_role(job_title, role_family, ""):
        return [
            "AI Product Design Operating System",
            "RWLV Priority Governor Agent",
            "Job Fit Agent",
        ]
    if _is_solutions_or_transformation_role(job_title, role_family, description):
        return [
            "AI Product Design Operating System",
            "Job Fit Agent",
            "RWLV Priority Governor Agent",
        ]
    if _is_analytics_product_systems_role(job_title, role_family, description):
        return [
            "RWLV Priority Governor Agent",
            "AI Product Design Operating System",
            "Job Fit Agent",
        ]
    if _matches_any(text, ("ai", "agent", "llm", "workflow", "automation", "builder")):
        return [
            "AI Product Design Operating System",
            "Job Fit Agent",
            "RWLV Priority Governor Agent",
        ]
    if any(term in text for term in hospitality_terms):
        return [
            "AI Product Design Operating System",
            "Resorts World digital experience work",
            "Hospitality API Integration Exploration",
        ]
    return [
        "AI Product Design Operating System",
        "Job Fit Agent",
        "RWLV Priority Governor Agent",
    ]


def _headline_for_role(job_title: str, role_family: str, description: str) -> str:
    if _is_solutions_or_transformation_role(job_title, role_family, description):
        return "Technical Product Builder | AI Workflow Systems | Product Analytics | Solutions Engineering"
    if _is_analytics_product_systems_role(job_title, role_family, description):
        return "Product Systems Builder | Product Analytics | AI Workflow Systems | Internal Tools"
    return "Technical Product Manager | AI Workflows | Product Systems | Agentic Operations"


def _summary_positioning_for_role(job_title: str, role_family: str, description: str) -> str:
    if _is_solutions_or_transformation_role(job_title, role_family, description):
        return (
            "Technical product and AI workflow builder focused on translating ambiguous business needs "
            "into usable internal tools, customer-facing solution workflows, and measurable product systems."
        )
    return (
        "Technical product builder focused on AI-enabled workflow systems, internal tools, "
        "product analytics, and agentic operations."
    )


def _role_strategy_emphasis(job_title: str, role_family: str, description: str) -> list[str]:
    if _is_solutions_or_transformation_role(job_title, role_family, description):
        return [
            "customer-facing technical problem solving",
            "AI workflow implementation",
            "product analytics and instrumentation",
            "translating ambiguous requirements into usable workflows",
            "API-connected systems and internal tools",
            "stakeholder communication and technical discovery",
            "implementation readiness and measurable product outcomes",
        ]
    return [
        "workflow automation",
        "product analytics",
        "product systems",
        "AI agents",
        "cross-functional execution",
    ]


def _project_bullet(project_name: str) -> str:
    bullets = {
        "AI Product Design Operating System": "AI Product Design Operating System: modular AI-assisted product design workflow using Current State, Component Inventory, Recommendation, Concept Generation, and Concept Evaluation agents to turn product context into structured recommendations and evaluable concept directions.",
        "Job Fit Agent": "Job Fit Agent: role discovery, scoring, status tracking, GitHub Actions scheduling, and Telegram notifications that support repeatable application operations.",
        "RWLV Priority Governor Agent": "RWLV Priority Governor Agent: internal operational AI enablement for intake triage, prioritization, and execution rhythm management.",
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
        "elevenlabs": "ElevenLabs",
    }
    company = company_display_names.get(raw_company.lower(), raw_company.title())
    title = (safe_row_value(job, "title", "Role") or "Role").strip()
    role_family = (safe_row_value(job, "role_family", "") or "").strip()
    jd_line = "Based on the role description, the responsibilities appear to value clear ownership, strong delivery habits, and measurable product outcomes."
    jd_context = "Based on the role description," if description else "Based on the available role details,"
    if _is_solutions_or_transformation_role(title, role_family, description):
        fit_paragraph = (
            "I fit this role through hands-on work translating ambiguous business needs into usable AI workflow systems, internal tools, "
            "API-connected operating workflows, and analytics-backed implementation plans. I am comfortable with customer-facing technical discovery, "
            "stakeholder communication, implementation readiness, and keeping solution design grounded in measurable product outcomes."
        )
        project_paragraph = (
            "Relevant project work includes AI Product Design Operating System as evidence of building agentic product workflows across current-state analysis, "
            "component inventory, recommendation, concept generation, and concept evaluation; Job Fit Agent as evidence of practical automation with GitHub Actions, "
            "Telegram, scoring, and status tracking; and RWLV Priority Governor Agent as evidence of internal operational AI enablement and triage."
        )
    else:
        fit_paragraph = (
            "I fit this role through hands-on work across product systems, workflow automation, analytics instrumentation, AI workflows, digital experience, "
            "and cross-functional execution. I work closely with partners across product, engineering, operations, and analytics to define scope, ship improvements, "
            "and keep execution grounded in clear signals from users and internal teams."
        )
        project_paragraph = (
            "Relevant project work includes AI Product Design Operating System for agentic product/design workflows, Job Fit Agent for practical workflow automation, "
            "and RWLV Priority Governor Agent for internal operational AI enablement and prioritization. These projects show how I approach repeatable execution, "
            "instrumentation quality, and practical AI-assisted workflows without over-claiming scope."
        )
    return f"""Cody McKeon
Las Vegas / Henderson Metro
760-669-9343
mckeonc0827@gmail.com

Dear {company} Hiring Team,

I am applying for the {title} role at {company}. I am interested in the opportunity because it aligns with the way I like to work: building practical systems that improve execution quality and speed. {jd_context} this role appears to value clear ownership, strong delivery habits, and measurable product outcomes. {jd_line}

{fit_paragraph}

{project_paragraph}

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
    top_projects = prioritized_projects[:3]
    recommended_headline = _headline_for_role(job["title"], role_family, description)
    summary_positioning = _summary_positioning_for_role(job["title"], role_family, description)
    strategy_emphasis = _role_strategy_emphasis(job["title"], role_family, description)

    resume_strategy = f"""# Resume Strategy

## Recommended headline
- {recommended_headline}

## Recommended summary angle
- {summary_positioning}

## Top skills to emphasize
{chr(10).join(f'- {skill}' for skill in strategy_emphasis)}

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
{summary_positioning}

## Tailored Summary
Aligned to {job['company']}'s {job['title']} role by emphasizing {top_strengths}, role-relevant project work, and verified experience from the base resume only.

## Experience Highlights
{base_resume}

## Selected Projects
{project_lines}

## Targeted Value for {job['company']} - {job['title']}
- Translate ambiguous requirements into usable AI-enabled workflows, internal tools, and implementation-ready plans.
- Improve product instrumentation and analytics quality to connect decisions to user behavior.
- Create practical agentic workflows that reduce manual process overhead and clarify stakeholder tradeoffs.

## Notes
- Do not add metrics unless validated from source records.
- Keep claims scoped to verified ownership and contribution.

## Resume Rules Applied
{resume_rule_text}
"""

    recruiter_note = f"""Hi, I am interested in the {job['title']} role at {job['company']}.
I focus on AI-enabled workflow systems, internal tools, product systems, and product analytics.
Relevant projects include AI Product Design Operating System for agentic product workflows, Job Fit Agent for GitHub Actions/Telegram automation with scoring and status tracking, and RWLV Priority Governor Agent for internal operational AI enablement and triage.
In my current work, I translate ambiguous requests into trackable requirements, analytics instrumentation, and implementation-ready digital work with marketing, web, analytics, and vendor partners.
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
    (app_dir / "submit_resume.md").write_text(_normalize_submit_resume(base_resume, headline=recommended_headline), encoding="utf-8")
    (app_dir / "recruiter_note.md").write_text(recruiter_note, encoding="utf-8")
    (app_dir / "answer_bank.md").write_text(answer_bank, encoding="utf-8")
    (app_dir / "risk_flags.md").write_text(risk_flags, encoding="utf-8")
    (app_dir / "cover_letter.md").write_text(cover_letter, encoding="utf-8")

    job_metadata = dict(job)
    package_metadata = {
        "job_id": job_id,
        "company": safe_row_value(job_metadata, "company", ""),
        "title": safe_row_value(job_metadata, "title", ""),
        "url": safe_row_value(job_metadata, "url", ""),
        "stable_job_key": _stable_job_key_for_job(job_metadata),
        "mobile_command_alias": mobile_command_alias_for_job(job_metadata),
    }
    (app_dir / "application_metadata.json").write_text(json.dumps(package_metadata, indent=2), encoding="utf-8")

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
        f"For this question at {_display_company_name(company)}, I would use concrete examples from three projects: AI Product Design Operating System (agentic product/design workflow), "
        "Job Fit Agent (AI-assisted role discovery and application drafting), and RWLV Priority Governor Agent (internal operational AI enablement and prioritization). I would tailor the example to the prompt, "
        "explain the user/problem context, the implementation approach, and how outcomes were reviewed with a human-in-the-loop before any external use."
    )
    notes = (
        "Choose the strongest matching project for this specific prompt; confirm scope boundaries and timeline language; avoid numeric impact claims unless you can verify them."
    )
    return answer, notes



DEFAULT_RESUME_HEADLINE = "Technical Product Manager | AI Workflows | Product Systems | Agentic Operations"


def _resume_submit_header(headline: str = DEFAULT_RESUME_HEADLINE) -> str:
    return f"""# Cody McKeon

Las Vegas / Henderson Metro  
760-669-9343 | mckeonc0827@gmail.com | https://github.com/cody-mckeon  

**{headline}**
"""


def _ensure_submit_resume_header(markdown_text: str, headline: str = DEFAULT_RESUME_HEADLINE) -> str:
    body = markdown_text.replace("\r\n", "\n")
    body_without_h1 = re.sub(r"^\s*#\s+.+\n+", "", body, count=1, flags=re.MULTILINE)
    body_without_contact = re.sub(r"^\s*Las Vegas / Henderson Metro\s*\n?", "", body_without_h1, count=1, flags=re.MULTILINE)
    body_without_contact = re.sub(r"^\s*760-669-9343\s*\|\s*mckeonc0827@gmail\.com\s*\|\s*https://github\.com/cody-mckeon\s*\n?", "", body_without_contact, count=1, flags=re.MULTILINE)
    body_without_headline = re.sub(r"^\s*\*\*.*?\|.*?\*\*\s*\n?", "", body_without_contact, count=1, flags=re.MULTILINE)
    body_without_headline = body_without_headline.lstrip("\n")
    header = _resume_submit_header(headline)
    return f"{header}\n{body_without_headline.lstrip()}" if body_without_headline.strip() else f"{header}\n"


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


def _normalize_submit_resume(markdown_text: str, headline: str = DEFAULT_RESUME_HEADLINE) -> str:
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
    return _ensure_submit_resume_header(normalized_text, headline=headline)


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
    job = _merge_persistent_status(dict(job))
    status = str(safe_row_value(job, "status", "")).lower()
    classification = str(safe_row_value(job, "classification", "")).lower()
    viability_level = str(safe_row_value(job, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(job, "geographic_eligibility", "review")).lower()
    application_status = str(safe_row_value(job, "application_status", "not_applied") or "not_applied").lower()
    if status not in {"new", "interested"}:
        return False
    return _is_actionable_job(job, require_role_overlap=True)


def _has_prep_eligible_role_overlap(job: dict[str, Any]) -> bool:
    classification = str(safe_row_value(job, "classification", "")).lower()
    if classification in {"high_fit", "apply_now"}:
        return True
    title = str(safe_row_value(job, "title", "")).lower()
    role_text = " ".join(
        str(safe_row_value(job, key, "") or "").lower()
        for key in ("title", "notes", "viability_reasons", "reasons", "red_flags", "role_family")
    )
    has_strong_overlap = any(term in role_text for term in STRONG_OVERLAP_TERMS)
    is_strong_role = any(term in title for term in STRONG_FIT_ROLE_TERMS)
    is_adjacent_role = any(term in title for term in ADJACENT_ROLE_TERMS)
    is_weak_role = any(term in title for term in WEAK_ROLE_TERMS)
    if is_weak_role and not has_strong_overlap:
        return False
    return is_strong_role or is_adjacent_role or has_strong_overlap


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


def _prep_next_application_score(job: dict[str, Any]) -> int:
    try:
        return int(safe_row_value(job, "score", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _prep_next_application_meets_min_score(job: dict[str, Any], min_score: int | None) -> bool:
    return min_score is None or _prep_next_application_score(job) >= min_score


def _prep_next_application_no_min_score_match(min_score: int) -> dict[str, Any]:
    return {
        "actionable": False,
        "message": "No eligible jobs found at or above min_score.",
        "min_score": min_score,
        "next_action": "lower min_score, include review jobs, or expand sources",
    }


def _prep_next_application_rank_key(job: dict[str, Any]) -> tuple[int, int, int, int, int]:
    classification_rank = {"high_fit": 0, "apply_now": 0, "near_fit": 1}
    viability_rank = {"apply_now": 0, "strong_review": 1}
    geography_rank = {"eligible": 0, "remote_us": 0}
    status_rank = {"new": 0, "interested": 0}
    return (
        classification_rank.get(str(safe_row_value(job, "classification", "")).lower(), 99),
        viability_rank.get(str(safe_row_value(job, "viability_level", "")).lower(), 99),
        -_prep_next_application_score(job),
        geography_rank.get(str(safe_row_value(job, "geographic_eligibility", "")).lower(), 99),
        status_rank.get(str(safe_row_value(job, "status", "")).lower(), 99),
    )


def _get_prep_next_application_candidates() -> list[dict[str, Any]]:
    initialize()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM jobs").fetchall()
    return [_merge_persistent_status(dict(row)) for row in rows if _is_prep_next_application_eligible(dict(row))]


def _is_actionable_selected_job(job: dict[str, Any]) -> bool:
    job = _merge_persistent_status(dict(job))
    status = str(safe_row_value(job, "status", "")).lower()
    classification = str(safe_row_value(job, "classification", "")).lower()
    viability_level = str(safe_row_value(job, "viability_level", "review")).lower()
    geographic_eligibility = str(safe_row_value(job, "geographic_eligibility", "review")).lower()
    application_status = str(safe_row_value(job, "application_status", "not_applied") or "not_applied").lower()
    return _is_actionable_job(job, require_role_overlap=True)




def _create_application_package_zip(app_dir: Path) -> tuple[Path, bool]:
    zip_path = app_dir.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=path.relative_to(Path(".")))
    return zip_path, zip_path.exists()

def _build_github_actions_run_url() -> str | None:
    server_url = str(os.getenv("GITHUB_SERVER_URL", "")).strip().rstrip("/")
    repository = str(os.getenv("GITHUB_REPOSITORY", "")).strip().strip("/")
    run_id = str(os.getenv("GITHUB_RUN_ID", "")).strip()
    if not (server_url and repository and run_id):
        return None
    return f"{server_url}/{repository}/actions/runs/{run_id}"


def prep_next_application(
    dry_run: bool = False,
    job_id: int | None = None,
    skip_browser: bool = False,
    force: bool = False,
    skip_pdf: bool = False,
    include_review: bool = False,
    min_score: int | None = None,
) -> dict[str, Any] | None:
    initialize()
    selected_job = None
    forced_below_min_score = False
    min_score_warning: str | None = None
    if job_id is not None:
        row = get_job_by_id(job_id)
        if row is None:
            print(json.dumps({"error": f"Job not found: {job_id}"}))
            return None
        selected_job = dict(row)
        selected_job_actionable = _is_actionable_selected_job(selected_job)
        selected_geo = str(safe_row_value(selected_job, "geographic_eligibility", "review")).lower()
        if not selected_job_actionable and selected_geo == "review" and include_review:
            selected_job_actionable = True
        if not selected_job_actionable and not force:
            print("Job is not actionable. Use --force to prepare anyway.")
            print(json.dumps({"error": "Job is not actionable. Use --force to prepare anyway."}))
            return None
        if not _prep_next_application_meets_min_score(selected_job, min_score):
            if not force:
                payload = _prep_next_application_no_min_score_match(int(min_score or 0))
                payload.update({
                    "job_id": int(selected_job["id"]),
                    "score": selected_job.get("score"),
                })
                print(json.dumps(payload, indent=2))
                return payload
            forced_below_min_score = True
            min_score_warning = f"Prepared despite score below min_score because --force was used."
    else:
        candidates = [job for job in _get_prep_next_application_candidates() if _is_prep_next_application_eligible(job)]
        if min_score is not None:
            candidates = [job for job in candidates if _prep_next_application_meets_min_score(job, min_score)]
            if not candidates:
                payload = _prep_next_application_no_min_score_match(min_score)
                print(json.dumps(payload, indent=2))
                return payload
        elif not candidates:
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
    if forced_below_min_score:
        selected_job_actionable = False
    questions_created = False
    answers_created = False

    pdf_export_status = "skipped" if dry_run else "generated"
    pdf_skipped = bool(dry_run or skip_pdf)
    resume_pdf_path_value: str | None = None if pdf_skipped else str(resume_pdf_path)
    package_zip_path: str | None = None
    package_zip_created = False
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
        try:
            zip_path, zip_created = _create_application_package_zip(app_dir)
            package_zip_path = str(zip_path)
            package_zip_created = zip_created
        except Exception:
            warnings.append("Package zip creation failed")

        if str(selected_job.get("status", "")).lower() not in {"applied", "interviewing", "rejected", "archived"}:
            update_status(selected_job_id, "applying")
        try:
            refreshed_job = get_job_by_id(selected_job_id)
        except Exception:
            refreshed_job = None
        if refreshed_job is not None:
            selected_job = dict(refreshed_job)

    stable_job_key = _stable_job_key_for_job(selected_job)
    mobile_command_alias = mobile_command_alias_for_job(selected_job)

    summary: dict[str, Any] = {
        "job_id": selected_job_id,
        "stable_job_key": stable_job_key,
        "mobile_command_alias": mobile_command_alias,
        "company": selected_job.get("company"),
        "title": selected_job.get("title"),
        "source": selected_job.get("source"),
        "external_job_id": _external_job_id_for_job(selected_job),
        "url": selected_job.get("url"),
        "score": selected_job.get("score"),
        "classification": selected_job.get("classification"),
        "viability_level": selected_job.get("viability_level"),
        "geographic_eligibility": selected_job.get("geographic_eligibility"),
        "geographic_reason": selected_job.get("geographic_reason"),
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
        "package_zip_path": package_zip_path,
        "package_zip_created": package_zip_created,
        "actionable": selected_job_actionable,
        "min_score": min_score,
        "skip_browser": skip_browser,
        "pdf_export": pdf_export_status,
        "application_tracking": _application_tracking_counts(),
    }
    if questions_created:
        summary["application_questions_path"] = str(app_dir / "application_questions.yaml")
    if answers_created:
        summary["application_answers_path"] = str(app_dir / "application_answers.md")
    warnings.extend(message for message in _geography_warnings_for_job(selected_job) if message not in warnings)
    if warning:
        summary["warning"] = warning
    if min_score_warning:
        summary["warning"] = min_score_warning
        warnings.append(min_score_warning)
    if force and not selected_job_actionable and not min_score_warning:
        forced_geo = str(selected_job.get("geographic_eligibility", "")).lower()
        forced_classification = str(selected_job.get("classification", "")).lower()
        forced_viability = str(selected_job.get("viability_level", "")).lower()
        if forced_geo in {"review", "ineligible"} and forced_classification in {"high_fit", "near_fit", "apply_now"} and forced_viability in {"apply_now", "strong_review"}:
            summary["warning"] = "Warning: geography is not eligible/requires review."
        else:
            summary["warning"] = "Prepared despite non-actionable status because --force was used."
    if warnings:
        summary["warnings"] = warnings
    github_actions_run_url = _build_github_actions_run_url()
    if github_actions_run_url:
        summary["github_actions_run_url"] = github_actions_run_url
    print(json.dumps(summary, indent=2))
    return summary


def _format_prep_next_application_telegram_message(summary: dict[str, Any]) -> str:
    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return []

    resume_pdf_line = f"Resume PDF: {summary.get('resume_pdf_path', '')}"
    if summary.get("resume_pdf_path"):
        resume_pdf_line = "Resume PDF: included"
    else:
        resume_pdf_line = "Resume PDF: failed or skipped, use submit_resume.md"

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

    tracking = summary.get("application_tracking")
    if isinstance(tracking, dict):
        lines.extend(
            [
                "",
                "Application tracking",
                f"Unapplied high-fit: {tracking.get('unapplied_high_fit_count', 0)}",
                f"Applied: {tracking.get('applied_count', 0)}",
                f"Interviewing: {tracking.get('interviewing_count', 0)}",
                f"Rejected: {tracking.get('rejected_count', 0)}",
                f"Offer: {tracking.get('offer_count', 0)}",
                f"Withdrawn: {tracking.get('withdrawn_count', 0)}",
                f"Saved: {tracking.get('saved_count', 0)}",
                f"Skipped: {tracking.get('skipped_count', 0)}",
                f"Blocked: {tracking.get('blocked_count', 0)}",
            ]
        )

    mobile_alias = str(summary.get("mobile_command_alias", "")).strip()
    stable_job_key = str(summary.get("stable_job_key", "")).strip()
    if mobile_alias or stable_job_key:
        lines.extend(["", "Telegram status commands"])
    if stable_job_key:
        lines.extend(
            [
                "After applying:",
                "```",
                f"applied {stable_job_key}",
                "```",
                "If rejected:",
                "```",
                f"rejected {stable_job_key}",
                "```",
                "If interviewing:",
                "```",
                f"interviewing {stable_job_key}",
                "```",
            ]
        )
    if mobile_alias:
        lines.extend(
            [
                "Mobile shortcut:",
                "```",
                f"applied {mobile_alias}",
                "```",
                "To skip:",
                "```",
                f"skip {mobile_alias} Not a fit",
                "```",
                "To save:",
                "```",
                f"save {mobile_alias}",
                "```",
            ]
        )

    warnings: list[str] = []
    if summary.get("warning") == "application question extraction failed; inspect manually":
        warnings.append("Browser extraction failed: inspect manually.")
    if summary.get("skip_browser"):
        warnings.append("Browser extraction skipped (--skip-browser).")
    if summary.get("pdf_export") in {"failed", "skipped"}:
        warnings.append(f"PDF export {summary.get('pdf_export')}.")
    if summary.get("pdf_skipped") or summary.get("pdf_export") == "failed":
        warnings.append("PDF export failed or skipped. Review submit_resume.md instead.")
    for warning_message in _as_list(summary.get("warnings")):
        if warning_message not in warnings:
            warnings.append(warning_message)
    viability = str(summary.get("viability_level", "")).lower()
    if viability in {"stretch", "review"}:
        warnings.append("Job is stretch/review, not apply_now.")
    geo = str(summary.get("geographic_eligibility", "")).lower()
    if geo == "review":
        if GEOGRAPHY_REVIEW_WARNING not in warnings:
            warnings.append(GEOGRAPHY_REVIEW_WARNING)
    elif geo == "ineligible":
        if GEOGRAPHY_REVIEW_WARNING not in warnings:
            warnings.append(GEOGRAPHY_REVIEW_WARNING)
    if warnings:
        lines.extend(["", "Warnings", *warnings])

    lines.extend(["", "Download package", "Package zip attached below."])
    github_actions_run_url = str(summary.get("github_actions_run_url", "")).strip()
    if github_actions_run_url:
        lines.extend(
            [
                f"GitHub Actions run: {github_actions_run_url}",
                "Backup: GitHub Actions artifact available in workflow run.",
            ]
        )
    lines.extend(
        [
            "Generated files are available in this run's artifact: job-fit-application-package-<run_id>.",
            "Download: GitHub → Actions → Job Fit Agent → latest run → Artifacts.",
            "If resume PDF export fails, use submit_resume.md for manual submission.",
        ]
    )
    lines.extend(["", "Next action: Review materials manually before submitting."])
    return "\n".join(lines)



PREP_NEXT_APPLICATION_USAGE = (
    "Usage: python -m job_fit_agent.main prep-next-application "
    "[--dry-run] [--job-id <id>] [--min-score <n>] [--include-review] "
    "[--force] [--skip-browser] [--skip-pdf] [--notify-telegram]"
)


def _parse_prep_next_application_args(tokens: list[str]) -> dict[str, Any] | None:
    if "--help" in tokens or "-h" in tokens:
        print(PREP_NEXT_APPLICATION_USAGE)
        return None

    allowed_flags = {
        "--dry-run",
        "--job-id",
        "--min-score",
        "--include-review",
        "--force",
        "--skip-browser",
        "--skip-pdf",
        "--notify-telegram",
    }
    parsed: dict[str, Any] = {
        "dry_run": False,
        "job_id": None,
        "min_score": None,
        "include_review": False,
        "force": False,
        "skip_browser": False,
        "skip_pdf": False,
        "notify_telegram": False,
    }

    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token not in allowed_flags:
            print(f"Error: unknown prep-next-application option: {token}")
            print(PREP_NEXT_APPLICATION_USAGE)
            raise SystemExit(2)
        if token in {"--job-id", "--min-score"}:
            if idx + 1 >= len(tokens) or tokens[idx + 1].startswith("--"):
                print(f"Error: {token} requires an integer value")
                print(PREP_NEXT_APPLICATION_USAGE)
                raise SystemExit(2)
            try:
                value = int(tokens[idx + 1])
            except ValueError:
                print(f"Error: {token} requires an integer value")
                print(PREP_NEXT_APPLICATION_USAGE)
                raise SystemExit(2)
            parsed["job_id" if token == "--job-id" else "min_score"] = value
            idx += 2
            continue
        parsed[{
            "--dry-run": "dry_run",
            "--include-review": "include_review",
            "--force": "force",
            "--skip-browser": "skip_browser",
            "--skip-pdf": "skip_pdf",
            "--notify-telegram": "notify_telegram",
        }[token]] = True
        idx += 1

    return parsed




def _parse_work_option_args(tokens: list[str], value_options: set[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token not in value_options:
            raise ValueError(f"Unknown option: {token}")
        if idx + 1 >= len(tokens) or tokens[idx + 1].startswith("--"):
            raise ValueError(f"{token} requires a value.")
        parsed[token[2:].replace("-", "_")] = tokens[idx + 1]
        idx += 2
    return parsed


def print_work_opportunities() -> None:
    """Print Work Opportunity Engine records grouped by execution status."""
    grouped = grouped_work_opportunities()
    print("Work Opportunity Engine")
    for section, title in WORK_SECTION_ORDER:
        print(title)
        rows = grouped.get(section, [])
        if not rows:
            print("No opportunities.")
            print()
            continue
        for row in rows:
            print(f"opportunity_id: {row.get('opportunity_id', '')}")
            print(f"title: {row.get('title', '')}")
            print(f"company: {row.get('company', '')}")
            print(f"opportunity_type: {row.get('opportunity_type', '')}")
            print(f"source: {row.get('source', '')}")
            print(f"priority: {row.get('priority', '')}")
            print(f"fit_score: {row.get('fit_score', '')}")
            print(f"revenue_potential: {row.get('revenue_potential', '')}")
            print(f"relationship_value: {row.get('relationship_value', '')}")
            if row.get("deadline"):
                print(f"deadline: {row.get('deadline', '')}")
            if row.get("blocked_until"):
                print(f"blocked_until: {row.get('blocked_until', '')}")
            print(f"next_action: {row.get('next_action', '')}")
            if row.get("why_fit"):
                print(f"why_fit: {row.get('why_fit', '')}")
            if row.get("notes"):
                print(f"notes: {row.get('notes', '')}")
            print("-")
        print()


def print_opportunity_review() -> None:
    """Print the highest-leverage next action across W2 and non-W2 opportunities."""
    print("Best work opportunity action today")
    print(json.dumps(opportunity_review(), indent=2))

def print_opportunity_pipeline() -> None:
    """Print company-centered Opportunity Pipeline grouped by strategy status."""
    grouped = grouped_pipeline(build_opportunity_pipeline())
    print("Opportunity Pipeline")
    for status, title in SECTION_ORDER:
        print(title)
        rows = grouped.get(status, [])
        if not rows:
            print("No companies.")
            print()
            continue
        for row in rows:
            print(f"company: {row.get('company', '')}")
            print(f"priority: {row.get('priority', '')}")
            print(f"best_job_score: {row.get('best_job_score', '')}")
            print(f"best_job_classification: {row.get('best_job_classification', '')}")
            print(f"best_job_viability_level: {row.get('best_job_viability_level', '')}")
            print(f"application_channel: {row.get('application_channel', '')}")
            if row.get("blocked_until"):
                print(f"blocked_until: {row.get('blocked_until', '')}")
            print(f"current_best_job_id: {row.get('current_best_job_id', '')}")
            print(f"current_best_job_url: {row.get('current_best_job_url', '')}")
            print(f"next_action: {row.get('next_action', '')}")
            notes = str(row.get("notes", "") or "")
            if notes:
                print(f"notes: {notes}")
            print("-")
        print()


def print_pipeline_review() -> None:
    """Print the best next Opportunity Pipeline action for today."""
    payload = pipeline_review()
    print("Best next action today")
    print(json.dumps(payload, indent=2))

def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]
    command = args[0] if args else "run"

    if command == "digest":
        print_digest(
            group_by_status="--group-by-status" in args[1:],
            include_skipped="--include-skipped" in args[1:],
        )
        return

    if command == "unapplied-high-fit":
        try:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else None
        except (ValueError, IndexError):
            print("Usage: python -m job_fit_agent.main unapplied-high-fit [--eligible-only] [--include-review] [--include-ineligible] [--limit <n>] [--json]")
            return
        print_unapplied_high_fit(
            eligible_only="--eligible-only" in args[1:],
            include_review=True,
            include_ineligible="--include-ineligible" in args[1:],
            limit=limit,
            as_json="--json" in args[1:],
        )
        return

    if command == "mark-applied":
        try:
            selected_job_id = int(args[args.index("--job-id") + 1]) if "--job-id" in args[1:] else None
            selected_url = args[args.index("--url") + 1] if "--url" in args[1:] else None
            note = args[args.index("--note") + 1] if "--note" in args[1:] else None
            mark_applied(job_id=selected_job_id, url=selected_url, note=note)
        except (ValueError, IndexError) as exc:
            print(str(exc) if str(exc) else "Usage: python -m job_fit_agent.main mark-applied (--job-id <id> | --url <job_url>) [--note <note>]")
        return

    if command == "mark-skipped":
        try:
            selected_job_id = int(args[args.index("--job-id") + 1]) if "--job-id" in args[1:] else None
            selected_url = args[args.index("--url") + 1] if "--url" in args[1:] else None
            reason = args[args.index("--reason") + 1] if "--reason" in args[1:] else None
            mark_skipped(job_id=selected_job_id, url=selected_url, reason=reason)
        except (ValueError, IndexError) as exc:
            print(str(exc) if str(exc) else "Usage: python -m job_fit_agent.main mark-skipped (--job-id <id> | --url <job_url>) [--reason <reason>]")
        return

    if command in {"block", "blocked"}:
        if command == "block" or (len(args) >= 2 and not args[1].startswith("--")):
            if len(args) < 3:
                print(f'Usage: python -m job_fit_agent.main {command} <job_id> "<reason>"')
                return
            try:
                mark_blocked(identifier=args[1], reason=" ".join(args[2:]) or None)
            except ValueError as exc:
                print(str(exc))
            return
        try:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else 50
        except (ValueError, IndexError):
            print("Usage: python -m job_fit_agent.main blocked <identifier> <reason> OR blocked [--limit <n>] [--json]")
            return
        print_blocked_jobs(limit=limit, as_json="--json" in args[1:])
        return


    if command == "block-company":
        if len(args) < 3:
            print('Usage: python -m job_fit_agent.main block-company <company> "<reason>" [--days <int> | --expires-at <YYYY-MM-DD>]')
            return
        try:
            company = args[1]
            remaining = list(args[2:])
            days = None
            expires_at = None
            if "--days" in remaining:
                idx = remaining.index("--days")
                days = int(remaining[idx + 1])
                del remaining[idx:idx + 2]
            if "--expires-at" in remaining:
                idx = remaining.index("--expires-at")
                expires_at = remaining[idx + 1]
                del remaining[idx:idx + 2]
            block_company(company, " ".join(remaining), days=days, expires_at=expires_at)
        except (ValueError, IndexError) as exc:
            print(str(exc) if str(exc) else 'Usage: python -m job_fit_agent.main block-company <company> "<reason>" [--days <int> | --expires-at <YYYY-MM-DD>]')
        return

    if command == "unblock-expired":
        unblock_expired_company_blocks()
        return



    if command == "work-opportunities":
        print_work_opportunities()
        return

    if command == "add-work-opportunity":
        usage = "Usage: python -m job_fit_agent.main add-work-opportunity --title <title> --company <company> --type <type> --source <source> [--source-detail <detail>] [--url <url>] [--priority <high|medium|low>] [--status <status>] [--why-fit <text>] [--deadline <YYYY-MM-DD>] [--next-action <text>] [--notes <text>]"
        try:
            parsed = _parse_work_option_args(args[1:], {"--title", "--company", "--type", "--source", "--source-detail", "--url", "--priority", "--status", "--why-fit", "--deadline", "--next-action", "--notes"})
            record = add_work_opportunity(
                title=parsed.get("title"),
                company=parsed.get("company"),
                opportunity_type=parsed.get("type"),
                source=parsed.get("source"),
                source_detail=parsed.get("source_detail", ""),
                url=parsed.get("url", ""),
                priority=parsed.get("priority", "medium"),
                status=parsed.get("status", "research"),
                why_fit=parsed.get("why_fit", ""),
                deadline=parsed.get("deadline", ""),
                next_action=parsed.get("next_action", ""),
                notes=parsed.get("notes", ""),
            )
        except ValueError as exc:
            print(str(exc))
            print(usage)
            return
        print(json.dumps(record, indent=2))
        return

    if command == "add-rfp":
        usage = "Usage: python -m job_fit_agent.main add-rfp --title <title> --organization <organization> [--url <url>] [--deadline <YYYY-MM-DD>] [--source <source>] [--source-detail <detail>] [--priority <high|medium|low>] [--why-fit <text>] [--notes <text>]"
        try:
            parsed = _parse_work_option_args(args[1:], {"--title", "--organization", "--url", "--deadline", "--source", "--source-detail", "--priority", "--why-fit", "--notes"})
            record = add_rfp(
                title=parsed.get("title"),
                organization=parsed.get("organization"),
                url=parsed.get("url", ""),
                deadline=parsed.get("deadline", ""),
                source=parsed.get("source", "government"),
                source_detail=parsed.get("source_detail", ""),
                priority=parsed.get("priority", "medium"),
                why_fit=parsed.get("why_fit", ""),
                notes=parsed.get("notes", ""),
            )
        except ValueError as exc:
            print(str(exc))
            print(usage)
            return
        print(json.dumps(record, indent=2))
        return

    if command == "opportunity-review":
        print_opportunity_review()
        return

    if command in {"prep-rfp", "prep-1099", "prep-local-outreach"}:
        if len(args) != 2:
            print(f"Usage: python -m job_fit_agent.main {command} <opportunity_id>")
            return
        prep_kind = {"prep-rfp": "rfp", "prep-1099": "1099", "prep-local-outreach": "local_outreach"}[command]
        try:
            payload = prep_work_opportunity(args[1], prep_kind)
        except ValueError as exc:
            print(str(exc))
            return
        print(json.dumps(payload, indent=2))
        return


    if command == "opportunity-pipeline":
        print_opportunity_pipeline()
        return

    if command == "pipeline-review":
        print_pipeline_review()
        return

    if command == "set-company-status":
        if len(args) != 4:
            print('Usage: python -m job_fit_agent.main set-company-status <company> <status> "<next_action>"')
            return
        try:
            record = set_company_status(args[1], args[2], args[3])
        except ValueError as exc:
            print(f"Error: {exc}")
            return
        # Refresh score-derived fields around the durable status override.
        records = build_opportunity_pipeline()
        refreshed = next((item for item in records if normalize_company_key(str(item.get("company", ""))) == normalize_company_key(str(record.get("company", "")))), record)
        print(json.dumps(refreshed, indent=2))
        return

    if command == "pipeline":
        try:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else 50
        except (ValueError, IndexError):
            print("Usage: python -m job_fit_agent.main pipeline [--limit <n>] [--json]")
            return
        print_pipeline_report(limit=limit, as_json="--json" in args[1:])
        return

    if command == "outcomes":
        try:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else 50
        except (ValueError, IndexError):
            print("Usage: python -m job_fit_agent.main outcomes [--limit <n>] [--json]")
            return
        print_outcomes_report(limit=limit, as_json="--json" in args[1:])
        return

    if command in {"rejected", "reject", "interviewing", "interview", "offer", "withdrawn", "withdraw"}:
        action = {"reject": "rejected", "interview": "interviewing", "withdraw": "withdrawn"}.get(command, command)
        if len(args) >= 2 and not args[1].startswith("--"):
            try:
                _mark_application_status(action, identifier=args[1], note=" ".join(args[2:]) or None)
            except ValueError as exc:
                print(str(exc))
            return
        if action == "rejected":
            try:
                limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else 50
            except (ValueError, IndexError):
                print("Usage: python -m job_fit_agent.main rejected <identifier> [note] OR rejected [--limit <n>] [--json]")
                return
            print_rejected_jobs(limit=limit, as_json="--json" in args[1:])
            return
        print(f"Usage: python -m job_fit_agent.main {command} <identifier> [note]")
        return

    if command == "applied":
        if len(args) == 2 and args[1].isdigit():
            try:
                mark_applied(job_id=int(args[1]))
            except ValueError as exc:
                print(str(exc))
            return
        if len(args) == 2 and not args[1].startswith("--"):
            try:
                mark_applied(identifier=args[1])
            except ValueError as exc:
                print(str(exc))
            return
        try:
            limit = int(args[args.index("--limit") + 1]) if "--limit" in args[1:] else 50
        except (ValueError, IndexError):
            print("Usage: python -m job_fit_agent.main applied <job_id> OR applied [--limit <n>] [--json]")
            return
        print_applied_jobs(limit=limit, as_json="--json" in args[1:])
        return

    if command == "skip":
        if len(args) < 3:
            print('Usage: python -m job_fit_agent.main skip <job_id> "<reason>"')
            return
        try:
            mark_skipped(job_id=int(args[1]), reason=" ".join(args[2:]))
        except ValueError as exc:
            print(str(exc))
        return

    if command == "save":
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main save <job_id>")
            return
        try:
            mark_saved(job_id=int(args[1]))
        except ValueError as exc:
            print(str(exc))
        return

    if command == "telegram-command":
        if len(args) != 2:
            print(json.dumps({"success": False, "message": 'Usage: python -m job_fit_agent.main telegram-command "applied 19"'}))
            return
        print(json.dumps(execute_telegram_status_command(args[1]), indent=2))
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

    if command == "prep-url":
        if len(args) < 2 or args[1].startswith("--"):
            print("Usage: python -m job_fit_agent.main prep-url <job_url> [--force] [--skip-browser] [--skip-pdf] [--notify-telegram] [--debug]")
            return
        try:
            prep_url(
                args[1],
                force="--force" in args[2:],
                skip_browser="--skip-browser" in args[2:],
                skip_pdf="--skip-pdf" in args[2:],
                notify_telegram="--notify-telegram" in args[2:],
                debug="--debug" in args[2:],
            )
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

    if command == "debug-geography":
        if len(args) == 1:
            location_audit()
            return
        if len(args) != 2:
            print("Usage: python -m job_fit_agent.main debug-geography [<job_id>]")
            return
        try:
            debug_geography(int(args[1]))
        except ValueError:
            print(f"Invalid job id: {args[1]}")
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
        parsed = _parse_prep_next_application_args(args[1:])
        if parsed is None:
            return
        notify_telegram = bool(parsed.pop("notify_telegram"))
        summary = prep_next_application(**parsed)
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
            package_zip_path = str(summary.get("package_zip_path", "")).strip()
            if package_zip_path and bool(summary.get("package_zip_created")):
                try:
                    send_document_with_credentials(
                        file_path=package_zip_path,
                        caption=(
                            f"Application package for {summary.get('title', '')}. "
                            "Review manually before submitting."
                        ),
                        bot_token=config.bot_token,
                        chat_id=config.chat_id,
                    )
                except Exception:
                    print("Telegram package upload failed")
        return

    print("python -m job_fit_agent.main run")
    print("python -m job_fit_agent.main digest")
    print("python -m job_fit_agent.main unapplied-high-fit [--eligible-only] [--include-ineligible] [--limit <n>] [--json]")
    print("python -m job_fit_agent.main mark-applied (--job-id <id> | --url <job_url>) [--note <note>]")
    print("python -m job_fit_agent.main mark-skipped (--job-id <id> | --url <job_url>) [--reason <reason>]")
    print('python -m job_fit_agent.main telegram-command "blocked <job_identifier> <reason>"')
    print('python -m job_fit_agent.main block <job_id> "<reason>"')
    print('python -m job_fit_agent.main block-company <company> "<reason>"')
    print("python -m job_fit_agent.main blocked [--limit <n>] [--json]")
    print("python -m job_fit_agent.main unblock-expired")
    print("python -m job_fit_agent.main applied <job_id>")
    print('python -m job_fit_agent.main skip <job_id> "<reason>"')
    print("python -m job_fit_agent.main save <job_id>")
    print('python -m job_fit_agent.main telegram-command "applied 19"')
    print("python -m job_fit_agent.main applied [--limit <n>] [--json]")
    print("python -m job_fit_agent.main rescore")
    print("python -m job_fit_agent.main work-opportunities")
    print("python -m job_fit_agent.main add-work-opportunity --title <title> --company <company> --type <type> --source <source> [--priority <priority>] [--status <status>]")
    print("python -m job_fit_agent.main add-rfp --title <title> --organization <organization> [--deadline <YYYY-MM-DD>]")
    print("python -m job_fit_agent.main opportunity-review")
    print("python -m job_fit_agent.main prep-rfp <opportunity_id>")
    print("python -m job_fit_agent.main prep-1099 <opportunity_id>")
    print("python -m job_fit_agent.main prep-local-outreach <opportunity_id>")
    print("python -m job_fit_agent.main opportunity-pipeline")
    print("python -m job_fit_agent.main pipeline-review")
    print('python -m job_fit_agent.main set-company-status <company> <status> "<next_action>"')
    print("python -m job_fit_agent.main set-status <job_id> <status>")
    print("python -m job_fit_agent.main list-status <status>")
    print('python -m job_fit_agent.main notes <job_id> "<note text>"')
    print("python -m job_fit_agent.main learn-url <job_url>")
    print("python -m job_fit_agent.main prep-url <job_url> [--force] [--skip-browser] [--skip-pdf] [--notify-telegram] [--debug]")
    print("python -m job_fit_agent.main promote-discovery <source> <company>")
    print("python -m job_fit_agent.main discover-companies")
    print("python -m job_fit_agent.main add-discovered-company <company> --source <source> --url <careers_url> --reason <reason>")
    print("python -m job_fit_agent.main approve-company <company>")
    print("python -m job_fit_agent.main reject-company <company>")
    print("python -m job_fit_agent.main debug-ashby-url <job_url>")
    print("python -m job_fit_agent.main location-audit")
    print("python -m job_fit_agent.main debug-geography [<job_id>]")
    print("python -m job_fit_agent.main prep-application <job_id>")
    print("python -m job_fit_agent.main export-resume-pdf <job_id>")
    print("python -m job_fit_agent.main extract-application-questions <job_id>")
    print("python -m job_fit_agent.main extract-application-questions-browser <job_id> [--debug]")
    print('python -m job_fit_agent.main add-application-question <job_id> "<question>"')
    print("python -m job_fit_agent.main generate-application-answers <job_id>")
    print("python -m job_fit_agent.main prep-next-application [--dry-run] [--job-id <id>] [--min-score <n>] [--force] [--skip-browser] [--skip-pdf] [--notify-telegram]")


if __name__ == "__main__":
    main()
