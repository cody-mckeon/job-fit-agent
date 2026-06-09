"""Durable application status persistence keyed by stable job keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_fit_agent.stable_identity import parse_stable_job_key_value

APPLICATION_STATUS_PATH = Path("data/application_status.json")
COMPANY_APPLICATION_BLOCKS_PATH = Path("data/company_application_blocks.json")
APPLICATION_STATUSES = {"not_applied", "saved", "applied", "interviewing", "rejected", "offer", "withdrawn", "skipped", "blocked"}
DURABLE_APPLICATION_STATUSES = APPLICATION_STATUSES - {"not_applied"}
TERMINAL_APPLICATION_STATUSES = {"rejected", "offer", "withdrawn", "skipped", "blocked"}
ACTIVE_APPLICATION_STATUSES = {"applied", "interviewing", "offer"}
EXCLUDED_FROM_AUTO_PREP_APPLICATION_STATUSES = {"saved", "applied", "interviewing", "rejected", "offer", "withdrawn", "skipped", "blocked"}
APPLICATION_STATUS_TIMESTAMP_FIELDS = {status: f"{status}_at" for status in DURABLE_APPLICATION_STATUSES}


def load_application_status(path: Path = APPLICATION_STATUS_PATH) -> dict[str, dict[str, Any]]:
    """Load durable application status records keyed by stable_job_key."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    if not raw:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("records"), dict):
        raw = raw["records"]
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid application status store: {path}")
    records: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            records[str(key)] = dict(value)
    return records


def save_application_status(records: dict[str, dict[str, Any]], path: Path = APPLICATION_STATUS_PATH) -> None:
    """Persist durable application status records in deterministic JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: records[key] for key in sorted(records)}
    path.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n")


def parse_stable_job_key(stable_job_key: str) -> tuple[str, str, str]:
    """Return source, company, and external job id parsed from a stable job key."""
    return parse_stable_job_key_value(stable_job_key)


def build_url_for_stable_key(source: str, company: str, external_job_id: str) -> str | None:
    """Build a job URL for stable keys whose source has a deterministic URL shape."""
    if source == "ashby":
        return f"https://jobs.ashbyhq.com/{company}/{external_job_id}"
    if source == "lever":
        return f"https://jobs.lever.co/{company}/{external_job_id}"
    return None


def normalize_company_key(company: str) -> str:
    """Normalize a company name for durable company-level application blocks."""
    return "".join(ch for ch in str(company or "").lower() if ch.isalnum())


def load_company_application_blocks(path: Path = COMPANY_APPLICATION_BLOCKS_PATH) -> dict[str, dict[str, Any]]:
    """Load durable company-level application blocks keyed by normalized company."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    if not raw:
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("records"), dict):
        raw = raw["records"]
    if isinstance(raw, list):
        raw = {normalize_company_key(item.get("company", "")): item for item in raw if isinstance(item, dict)}
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid company application block store: {path}")
    records: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            normalized = normalize_company_key(str(value.get("company") or key))
            if normalized:
                records[normalized] = dict(value)
    return records


def save_company_application_blocks(records: dict[str, dict[str, Any]], path: Path = COMPANY_APPLICATION_BLOCKS_PATH) -> None:
    """Persist company-level application blocks in deterministic JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: records[key] for key in sorted(records)}
    path.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n")
