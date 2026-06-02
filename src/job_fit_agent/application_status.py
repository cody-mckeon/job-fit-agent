"""Durable application status persistence keyed by stable job keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

APPLICATION_STATUS_PATH = Path("data/application_status.json")
APPLICATION_STATUSES = {"not_applied", "saved", "applied", "interviewing", "rejected", "offer", "withdrawn", "skipped"}
DURABLE_APPLICATION_STATUSES = APPLICATION_STATUSES - {"not_applied"}
TERMINAL_APPLICATION_STATUSES = {"rejected", "offer", "withdrawn", "skipped"}
ACTIVE_APPLICATION_STATUSES = {"applied", "interviewing", "offer"}
EXCLUDED_FROM_AUTO_PREP_APPLICATION_STATUSES = {"saved", "applied", "interviewing", "rejected", "offer", "withdrawn", "skipped"}
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
    parts = stable_job_key.strip().split(":", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError("Stable job key must use source:company:external_job_id.")
    return parts[0], parts[1], parts[2]


def build_url_for_stable_key(source: str, company: str, external_job_id: str) -> str | None:
    """Build a job URL for stable keys whose source has a deterministic URL shape."""
    if source == "ashby":
        return f"https://jobs.ashbyhq.com/{company}/{external_job_id}"
    if source == "lever":
        return f"https://jobs.lever.co/{company}/{external_job_id}"
    return None
