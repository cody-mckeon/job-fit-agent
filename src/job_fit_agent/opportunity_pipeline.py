"""Company-centered Opportunity Pipeline strategy layer.

The Opportunity Pipeline consumes scored jobs and application state, but it does
not replace or mutate the job scoring engine. It answers: what should Cody do
next?
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from job_fit_agent.application_status import (
    load_application_status,
    load_company_application_blocks,
    normalize_company_key,
)
from job_fit_agent.repository import DB_PATH, initialize

OPPORTUNITY_PIPELINE_PATH = Path("data/opportunity_pipeline.json")

VALID_PRIORITIES = {"high", "medium", "low"}
VALID_CHANNELS = {"direct", "recruiter", "referral", "linkedin", "email", "blocked", "unknown"}
VALID_PIPELINE_STATUSES = {
    "apply_now",
    "relationship_strategy",
    "blocked_cooldown",
    "research",
    "watch",
    "skipped",
}

TARGET_LANE_TERMS = (
    "ai agent", "ai agents", "agentic", "workflow automation", "workflow", "automation",
    "ai operations", "ai ops", "ai transformation", "ai implementation", "ai adoption",
    "product systems", "product analytics", "internal ai", "internal tools",
    "business process", "process improvement", "technical implementation", "solutions engineer",
    "solutions architect", "implementation", "revops", "revenue systems", "business systems",
    "gtm systems", "product operations", "ai product", "llm", "generative ai", "copilot",
)
GENERIC_OR_TOO_TECHNICAL_TERMS = (
    "backend", "infrastructure", "distributed systems", "platform engineer", "security engineer",
    "machine learning engineer", "research scientist", "data scientist", "compiler", "kernel",
)
RELATIONSHIP_COMPANIES = {"linear", "elevenlabs"}

SECTION_ORDER = (
    ("apply_now", "Apply now"),
    ("relationship_strategy", "Relationship strategy"),
    ("blocked_cooldown", "Blocked / cooldown"),
    ("research", "Research targets"),
    ("watch", "Watchlist"),
    ("skipped", "Skip"),
)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _row_value(row: dict[str, Any], key: str, default: Any = "") -> Any:
    value = row.get(key, default)
    return default if value is None else value


def _job_external_id(row: dict[str, Any]) -> str:
    url = str(_row_value(row, "url", ""))
    patterns = (
        r"^https://jobs\.ashbyhq\.com/[^/]+/([^/?#]+)",
        r"^https://(?:boards|job-boards)\.greenhouse\.io/[^/]+/jobs/([^/?#]+)",
        r"^https://jobs\.lever\.co/[^/]+/([^/?#]+)",
    )
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return match.group(1)
    return str(_row_value(row, "id", ""))


def _stable_job_key(row: dict[str, Any]) -> str:
    source = str(_row_value(row, "source", "job") or "job").lower()
    company = str(_row_value(row, "company", "company") or "company").lower()
    return f"{source}:{company}:{_job_external_id(row)}"


def load_opportunity_pipeline(path: Path = OPPORTUNITY_PIPELINE_PATH) -> list[dict[str, Any]]:
    """Load durable Opportunity Pipeline company records."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = raw.get("companies", raw.get("records", []))
    if not isinstance(raw, list):
        raise ValueError(f"Invalid opportunity pipeline store: {path}")
    return [dict(item) for item in raw if isinstance(item, dict)]


def save_opportunity_pipeline(records: list[dict[str, Any]], path: Path = OPPORTUNITY_PIPELINE_PATH) -> None:
    """Persist company records deterministically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda item: (str(item.get("priority", "medium")), str(item.get("company", "")).lower()))
    path.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n")


def _company_blocks() -> dict[str, dict[str, Any]]:
    try:
        return load_company_application_blocks()
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _active_company_block(company: str) -> dict[str, Any] | None:
    record = _company_blocks().get(normalize_company_key(company))
    if not record or str(record.get("status", "")).lower() != "blocked":
        return None
    expires_at = str(record.get("expires_at") or "")
    if not expires_at:
        return record
    try:
        if date.fromisoformat(expires_at[:10]) >= datetime.now(UTC).date():
            return record
    except ValueError:
        return record
    return None


def _application_status_by_key() -> dict[str, dict[str, Any]]:
    try:
        return load_application_status()
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _merge_application_status(row: dict[str, Any], records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = _stable_job_key(row)
    record = records.get(key)
    if not record:
        return row
    merged = dict(row)
    status = str(record.get("application_status") or "").lower()
    if status:
        merged["application_status"] = status
    if record.get("note"):
        merged["application_notes"] = record["note"]
    if record.get("blocked_at"):
        merged["blocked_at"] = record["blocked_at"]
    return merged


def load_scored_jobs() -> list[dict[str, Any]]:
    """Load scored job rows from SQLite without changing score data."""
    initialize()
    if not DB_PATH.exists():
        return []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = [dict(row) for row in conn.execute("SELECT * FROM jobs").fetchall()]
    status_records = _application_status_by_key()
    return [_merge_application_status(row, status_records) for row in rows]


def _combined_job_text(row: dict[str, Any]) -> str:
    fields = [
        "title", "company", "department", "team", "role_family", "classification",
        "reasons", "red_flags", "viability_reasons", "notes", "application_notes",
    ]
    return " ".join(str(_row_value(row, field, "")) for field in fields).lower()


def is_target_lane_job(row: dict[str, Any]) -> bool:
    text = _combined_job_text(row)
    return any(term in text for term in TARGET_LANE_TERMS)


def _has_too_technical_signal(row: dict[str, Any]) -> bool:
    text = _combined_job_text(row)
    return any(term in text for term in GENERIC_OR_TOO_TECHNICAL_TERMS)


def _is_geographically_eligible(row: dict[str, Any]) -> bool:
    geo = str(_row_value(row, "geographic_eligibility", "review") or "review").lower()
    raw = f"{_row_value(row, 'location_raw', '')} {_row_value(row, 'location', '')}".lower()
    if any(term in raw for term in ("europe", "emea", "apac", "latam", "uk", "germany", "canada")):
        return False
    return geo in {"eligible", "remote_us"}


def _is_job_blocked(row: dict[str, Any]) -> bool:
    if _active_company_block(str(_row_value(row, "company", ""))):
        return True
    if str(_row_value(row, "application_status", "not_applied") or "not_applied").lower() == "blocked":
        return True
    return str(_row_value(row, "status", "") or "").lower() == "blocked"


def _is_unavailable(row: dict[str, Any]) -> bool:
    if _is_job_blocked(row):
        return True
    app_status = str(_row_value(row, "application_status", "not_applied") or "not_applied").lower()
    return app_status in {"applied", "interviewing", "rejected", "offer", "withdrawn", "skipped"}


def _job_rank(row: dict[str, Any]) -> tuple[int, int, int, int, int]:
    classification_rank = {"high_fit": 0, "near_fit": 1, "low_fit": 2}
    viability_rank = {"apply_now": 0, "strong_review": 1, "review": 2, "weak": 3, "ineligible": 4}
    return (
        classification_rank.get(str(_row_value(row, "classification", "")).lower(), 9),
        viability_rank.get(str(_row_value(row, "viability_level", "")).lower(), 9),
        -int(_row_value(row, "score", 0) or 0),
        0 if is_target_lane_job(row) else 1,
        0 if _is_geographically_eligible(row) else 1,
    )


def _best_job_for_company(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(rows, key=_job_rank)[0]


def _infer_priority(existing: dict[str, Any], rows: list[dict[str, Any]], best: dict[str, Any] | None) -> str:
    current = str(existing.get("priority") or "").lower()
    if current in VALID_PRIORITIES:
        return current
    if best and is_target_lane_job(best) and str(_row_value(best, "classification", "")).lower() == "high_fit":
        return "high"
    if any(is_target_lane_job(row) for row in rows):
        return "medium"
    return "low"


def _role_families(rows: list[dict[str, Any]]) -> list[str]:
    families: set[str] = set()
    for row in rows:
        role_family = str(_row_value(row, "role_family", "") or "").strip()
        title = str(_row_value(row, "title", "") or "").strip()
        if role_family:
            families.add(role_family)
        elif title:
            families.add(title)
    return sorted(families)


def _why_fit(rows: list[dict[str, Any]], best: dict[str, Any] | None) -> str:
    if not best:
        return "Company is tracked for future AI agent, workflow automation, AI operations, product systems, analytics, or internal transformation roles."
    if is_target_lane_job(best):
        return "Current roles overlap Cody's target lane: AI agents, workflow automation, AI operations, product systems, analytics, or internal AI transformation."
    return "Current roles are not a strong target-lane match, but the company may be strategically useful to monitor."


def _status_for_company(company: str, existing: dict[str, Any], rows: list[dict[str, Any]], best: dict[str, Any] | None) -> str:
    existing_status = str(existing.get("status") or "").lower()
    if existing_status in VALID_PIPELINE_STATUSES:
        return existing_status
    if _active_company_block(company) or (best and _is_job_blocked(best)):
        return "blocked_cooldown"
    if not best:
        return "research"

    classification = str(_row_value(best, "classification", "")).lower()
    viability = str(_row_value(best, "viability_level", "")).lower()
    score = int(_row_value(best, "score", 0) or 0)
    company_key = normalize_company_key(company)

    if _is_unavailable(best):
        return "watch"
    if company_key in RELATIONSHIP_COMPANIES or viability in {"strong_review", "review"}:
        return "relationship_strategy"
    if not _is_geographically_eligible(best):
        return "research"
    if _has_too_technical_signal(best):
        return "relationship_strategy"
    if classification == "high_fit" and viability == "apply_now" and score >= 70 and is_target_lane_job(best):
        return "apply_now"
    if classification == "near_fit" and viability in {"apply_now", "strong_review"} and score >= 70 and is_target_lane_job(best):
        return "relationship_strategy"
    if classification == "near_fit" or score < 70:
        return "watch"
    return "research"


def _channel_for_status(status: str, existing: dict[str, Any]) -> str:
    current = str(existing.get("application_channel") or "").lower()
    if current in VALID_CHANNELS and current != "unknown":
        return current
    return {
        "apply_now": "direct",
        "relationship_strategy": "linkedin",
        "blocked_cooldown": "blocked",
        "research": "unknown",
        "watch": "unknown",
        "skipped": "unknown",
    }.get(status, "unknown")


def _next_action_for_status(company: str, status: str, best: dict[str, Any] | None, existing: dict[str, Any]) -> str:
    if existing.get("next_action"):
        return str(existing["next_action"])
    title = str(_row_value(best or {}, "title", "current roles") or "current roles")
    if status == "apply_now":
        return f"Apply to {company} {title} after a final manual review."
    if status == "relationship_strategy":
        return f"Manual review {company} {title} and pursue recruiter, referral, LinkedIn, or email context before applying."
    if status == "blocked_cooldown":
        block = _active_company_block(company)
        expires = str((block or {}).get("expires_at") or existing.get("blocked_until") or "the cooldown clears")
        return f"Wait until {expires} or pursue recruiter/manual review instead of direct apply."
    if status == "research":
        return f"Research {company} for AI implementation, operations, internal tools, product systems, and agent deployment openings."
    if status == "watch":
        return f"Watch {company} until a stronger AI operations, internal tools, automation, or agent deployment role appears."
    return f"Skip {company} unless strategy changes."


def build_opportunity_pipeline(*, persist: bool = True) -> list[dict[str, Any]]:
    existing_records = {normalize_company_key(str(item.get("company", ""))): dict(item) for item in load_opportunity_pipeline()}
    rows_by_company: dict[str, list[dict[str, Any]]] = {}
    for row in load_scored_jobs():
        company = str(_row_value(row, "company", "") or "").strip()
        if company:
            rows_by_company.setdefault(normalize_company_key(company), []).append(row)
            existing_records.setdefault(normalize_company_key(company), {"company": company})

    timestamp = _utc_timestamp()
    records: list[dict[str, Any]] = []
    for key, existing in existing_records.items():
        company = str(existing.get("company") or key)
        rows = rows_by_company.get(key, [])
        best = _best_job_for_company(rows)
        status = _status_for_company(company, existing, rows, best)
        block = _active_company_block(company)
        record = {
            "company": company,
            "priority": _infer_priority(existing, rows, best),
            "target_role_families": existing.get("target_role_families") or _role_families(rows),
            "why_fit": existing.get("why_fit") or _why_fit(rows, best),
            "current_best_job_id": _job_external_id(best) if best else existing.get("current_best_job_id"),
            "current_best_job_url": str(_row_value(best or {}, "url", "") or existing.get("current_best_job_url", "")),
            "best_job_score": int(_row_value(best or {}, "score", 0) or 0) if best else existing.get("best_job_score"),
            "best_job_classification": str(_row_value(best or {}, "classification", "") or existing.get("best_job_classification", "")),
            "best_job_viability_level": str(_row_value(best or {}, "viability_level", "") or existing.get("best_job_viability_level", "")),
            "application_channel": _channel_for_status(status, existing),
            "status": status,
            "blocked_until": str((block or {}).get("expires_at") or existing.get("blocked_until") or ""),
            "next_action": _next_action_for_status(company, status, best, existing),
            "notes": existing.get("notes", ""),
            "last_reviewed_at": timestamp,
        }
        records.append(record)
    if persist:
        save_opportunity_pipeline(records)
    return records


def set_company_status(company: str, status: str, next_action: str) -> dict[str, Any]:
    """Set a durable company pipeline status and next action."""
    normalized_status = status.strip().lower()
    if normalized_status not in VALID_PIPELINE_STATUSES:
        raise ValueError(f"Invalid company status '{status}'. Must be one of: {', '.join(sorted(VALID_PIPELINE_STATUSES))}.")
    company_value = company.strip()
    if not company_value:
        raise ValueError("Company is required.")
    if not next_action.strip():
        raise ValueError("next_action is required.")

    records = load_opportunity_pipeline()
    key = normalize_company_key(company_value)
    by_key = {normalize_company_key(str(item.get("company", ""))): dict(item) for item in records}
    record = by_key.get(key, {"company": company_value})
    record.update({
        "company": record.get("company") or company_value,
        "status": normalized_status,
        "next_action": next_action.strip(),
        "last_reviewed_at": _utc_timestamp(),
    })
    if normalized_status == "blocked_cooldown":
        record.setdefault("application_channel", "blocked")
    by_key[key] = record
    saved = list(by_key.values())
    save_opportunity_pipeline(saved)
    return record


def grouped_pipeline(records: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    records = records if records is not None else build_opportunity_pipeline()
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    grouped = {status: [] for status, _ in SECTION_ORDER}
    for record in records:
        status = str(record.get("status") or "watch")
        grouped.setdefault(status, []).append(record)
    for status, items in grouped.items():
        items.sort(key=lambda item: (priority_rank.get(str(item.get("priority", "medium")), 9), -int(item.get("best_job_score") or 0), str(item.get("company", "")).lower()))
    return grouped


def _review_rank(record: dict[str, Any]) -> tuple[int, int, int]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    status_rank = {"apply_now": 0, "relationship_strategy": 1, "research": 2, "blocked_cooldown": 3, "watch": 4, "skipped": 5}
    return (
        status_rank.get(str(record.get("status", "watch")), 9),
        priority_rank.get(str(record.get("priority", "medium")), 9),
        -int(record.get("best_job_score") or 0),
    )


def pipeline_review() -> dict[str, Any]:
    """Return the best next action today without modifying job scores."""
    records = build_opportunity_pipeline(persist=True)
    apply_now = [r for r in records if r.get("status") == "apply_now" and str(r.get("priority", "medium")) in {"high", "medium"}]
    if apply_now:
        chosen = sorted(apply_now, key=_review_rank)[0]
        return {
            "best_next_action_today": chosen["next_action"],
            "reasoning": "Best eligible apply-now company combines priority, job score, viability, geography, and Cody's target lane.",
            "recommended_company": chosen["company"],
            "recommended_job_id": chosen.get("current_best_job_id"),
            "recommended_channel": chosen.get("application_channel", "direct"),
            "why_not_simply_apply": "This recommendation is a direct application after manual review because no company-level block was detected.",
        }

    relationship = [r for r in records if r.get("status") == "relationship_strategy"]
    if relationship:
        chosen = sorted(relationship, key=_review_rank)[0]
        return {
            "best_next_action_today": chosen["next_action"],
            "reasoning": "No strong direct-apply role is available; the best leverage is relationship/manual-review work around a strategic company or stretch role.",
            "recommended_company": chosen["company"],
            "recommended_job_id": chosen.get("current_best_job_id"),
            "recommended_channel": chosen.get("application_channel", "linkedin"),
            "why_not_simply_apply": "The role needs relationship strategy, manual review, stretch calibration, or another channel before direct application.",
        }

    blocked = [r for r in records if r.get("status") == "blocked_cooldown"]
    if blocked:
        chosen = sorted(blocked, key=_review_rank)[0]
        return {
            "best_next_action_today": chosen["next_action"],
            "reasoning": "The strongest strategic signal is blocked for normal direct application, so Cody should use recruiter/manual review or wait.",
            "recommended_company": chosen["company"],
            "recommended_job_id": chosen.get("current_best_job_id"),
            "recommended_channel": "recruiter",
            "why_not_simply_apply": "Do not recommend blocked companies for normal direct application; cooldown or application state blocks direct apply.",
        }

    research = [r for r in records if r.get("status") in {"research", "watch"} and r.get("status") != "skipped"]
    if research:
        chosen = sorted(research, key=_review_rank)[0]
        return {
            "best_next_action_today": chosen["next_action"],
            "reasoning": "No strong eligible apply-now role exists; research/watch work is better than forcing a weak near-fit application.",
            "recommended_company": chosen["company"],
            "recommended_job_id": chosen.get("current_best_job_id"),
            "recommended_channel": chosen.get("application_channel", "unknown"),
            "why_not_simply_apply": "Weak, generic, geographically uncertain, or low-viability roles should not become best next action solely because they are eligible.",
        }

    return {
        "best_next_action_today": "Expand discovery beyond Ashby and research AI implementation firms.",
        "reasoning": "No scored jobs or strategic company records are available.",
        "recommended_company": None,
        "recommended_job_id": None,
        "recommended_channel": "research",
        "why_not_simply_apply": "There is no strong eligible job to apply to today.",
    }
