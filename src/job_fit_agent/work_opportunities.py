"""Durable Work Opportunity Engine for W2, 1099, RFP, vendor, and manual leads."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from job_fit_agent.application_status import load_company_application_blocks, normalize_company_key
from job_fit_agent.config import load_discovered_companies
from job_fit_agent.opportunity_pipeline import build_opportunity_pipeline, load_scored_jobs

WORK_OPPORTUNITIES_PATH = Path("data/work_opportunities.json")

VALID_OPPORTUNITY_TYPES = {
    "w2_job",
    "contract_1099",
    "fractional",
    "rfp",
    "vendor_opportunity",
    "local_business",
    "warm_intro",
    "manual_lead",
}
VALID_WORK_SOURCES = {
    "ashby",
    "greenhouse",
    "lever",
    "manual",
    "podcast",
    "linkedin",
    "yc",
    "local",
    "government",
    "referral",
    "other",
}
VALID_WORK_STATUSES = {
    "research",
    "qualify",
    "pursue",
    "proposal_needed",
    "relationship_strategy",
    "applied",
    "submitted",
    "won",
    "lost",
    "skipped",
    "blocked",
}
VALID_WORK_PRIORITIES = {"high", "medium", "low"}
VALID_REVENUE_POTENTIALS = {"unknown", "low", "medium", "high"}
VALID_RELATIONSHIP_VALUES = {"low", "medium", "high"}

TARGET_LANE_TERMS = (
    "ai agent",
    "ai agents",
    "agentic",
    "workflow automation",
    "workflow",
    "automation",
    "ai operations",
    "ai ops",
    "ai transformation",
    "ai implementation",
    "product systems",
    "technical implementation",
    "product analytics",
    "internal ai",
    "business process",
    "process improvement",
    "operations reporting",
    "internal tools",
)

WORK_SECTION_ORDER = (
    ("pursue_now", "Pursue now"),
    ("proposal_needed", "Proposal needed"),
    ("relationship_strategy", "Relationship strategy"),
    ("research_qualify", "Research / qualify"),
    ("blocked", "Blocked"),
    ("submitted_waiting", "Submitted / waiting"),
    ("won", "Won"),
    ("lost_skipped", "Lost / skipped"),
)

PREP_FILES = (
    "opportunity_brief.md",
    "qualification_checklist.md",
    "proposed_solution_outline.md",
    "risks.md",
    "outreach_note.md",
    "next_steps.md",
)


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "opportunity"


def _parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def load_work_opportunities(path: Path = WORK_OPPORTUNITIES_PATH) -> list[dict[str, Any]]:
    """Load durable Work Opportunity Engine records."""
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = raw.get("opportunities", raw.get("records", []))
    if not isinstance(raw, list):
        raise ValueError(f"Invalid work opportunities store: {path}")
    return [normalize_work_opportunity(item) for item in raw if isinstance(item, dict)]


def save_work_opportunities(records: list[dict[str, Any]], path: Path = WORK_OPPORTUNITIES_PATH) -> None:
    """Persist Work Opportunity Engine records deterministically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        (normalize_work_opportunity(record) for record in records),
        key=lambda item: (
            priority_rank.get(str(item.get("priority", "medium")), 9),
            str(item.get("deadline") or "9999-12-31"),
            str(item.get("company", "")).lower(),
            str(item.get("title", "")).lower(),
        ),
    )
    path.write_text(json.dumps(ordered, indent=2, sort_keys=True) + "\n")


def _validate_choice(field: str, value: str, valid: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in valid:
        raise ValueError(f"Invalid {field} '{value}'. Must be one of: {', '.join(sorted(valid))}.")
    return normalized


def _opportunity_id(title: str, company: str, opportunity_type: str, source: str, created_at: str) -> str:
    digest = hashlib.sha1(f"{title}|{company}|{opportunity_type}|{source}|{created_at}".encode()).hexdigest()[:12]
    return f"work-{digest}"


def normalize_work_opportunity(record: dict[str, Any]) -> dict[str, Any]:
    """Return a full Work Opportunity Engine record with all durable fields."""
    now = utc_timestamp()
    created_at = str(record.get("created_at") or now)
    title = str(record.get("title") or "").strip()
    company = str(record.get("company") or record.get("organization") or "").strip()
    opportunity_type = str(record.get("opportunity_type") or record.get("type") or "manual_lead").strip().lower()
    source = str(record.get("source") or "manual").strip().lower()
    if opportunity_type not in VALID_OPPORTUNITY_TYPES:
        opportunity_type = "manual_lead"
    if source not in VALID_WORK_SOURCES:
        source = "other"
    return {
        "opportunity_id": str(record.get("opportunity_id") or _opportunity_id(title, company, opportunity_type, source, created_at)),
        "title": title,
        "company": company,
        "opportunity_type": opportunity_type,
        "source": source,
        "source_detail": str(record.get("source_detail") or ""),
        "url": str(record.get("url") or ""),
        "status": str(record.get("status") or "research").strip().lower() if str(record.get("status") or "research").strip().lower() in VALID_WORK_STATUSES else "research",
        "priority": str(record.get("priority") or "medium").strip().lower() if str(record.get("priority") or "medium").strip().lower() in VALID_WORK_PRIORITIES else "medium",
        "fit_score": int(record.get("fit_score") or 0),
        "revenue_potential": str(record.get("revenue_potential") or "unknown").strip().lower() if str(record.get("revenue_potential") or "unknown").strip().lower() in VALID_REVENUE_POTENTIALS else "unknown",
        "relationship_value": str(record.get("relationship_value") or "medium").strip().lower() if str(record.get("relationship_value") or "medium").strip().lower() in VALID_RELATIONSHIP_VALUES else "medium",
        "deadline": str(record.get("deadline") or ""),
        "blocked_until": str(record.get("blocked_until") or ""),
        "next_action": str(record.get("next_action") or ""),
        "why_fit": str(record.get("why_fit") or ""),
        "risks": str(record.get("risks") or ""),
        "notes": str(record.get("notes") or ""),
        "created_at": created_at,
        "updated_at": str(record.get("updated_at") or now),
    }


def add_work_opportunity(**kwargs: Any) -> dict[str, Any]:
    """Create and persist one Work Opportunity Engine record."""
    title = str(kwargs.get("title") or "").strip()
    company = str(kwargs.get("company") or kwargs.get("organization") or "").strip()
    if not title:
        raise ValueError("title is required.")
    if not company:
        raise ValueError("company is required.")
    opportunity_type = _validate_choice("opportunity_type", str(kwargs.get("opportunity_type") or kwargs.get("type") or "manual_lead"), VALID_OPPORTUNITY_TYPES)
    source = _validate_choice("source", str(kwargs.get("source") or "manual"), VALID_WORK_SOURCES)
    status = _validate_choice("status", str(kwargs.get("status") or "research"), VALID_WORK_STATUSES)
    priority = _validate_choice("priority", str(kwargs.get("priority") or "medium"), VALID_WORK_PRIORITIES)
    now = utc_timestamp()
    record = normalize_work_opportunity({
        **kwargs,
        "title": title,
        "company": company,
        "opportunity_type": opportunity_type,
        "source": source,
        "status": status,
        "priority": priority,
        "created_at": now,
        "updated_at": now,
    })
    records = load_work_opportunities()
    records.append(record)
    save_work_opportunities(records)
    return record


def add_rfp(**kwargs: Any) -> dict[str, Any]:
    """Create an RFP work opportunity."""
    status = "proposal_needed" if kwargs.get("deadline") else "qualify"
    return add_work_opportunity(
        title=kwargs.get("title"),
        company=kwargs.get("organization"),
        opportunity_type="rfp",
        source=kwargs.get("source") or "government",
        source_detail=kwargs.get("source_detail") or "",
        url=kwargs.get("url") or "",
        deadline=kwargs.get("deadline") or "",
        priority=kwargs.get("priority") or "medium",
        status=status,
        why_fit=kwargs.get("why_fit") or "",
        notes=kwargs.get("notes") or "",
    )


def _section_for_status(status: str) -> str:
    return {
        "pursue": "pursue_now",
        "proposal_needed": "proposal_needed",
        "relationship_strategy": "relationship_strategy",
        "research": "research_qualify",
        "qualify": "research_qualify",
        "blocked": "blocked",
        "applied": "submitted_waiting",
        "submitted": "submitted_waiting",
        "won": "won",
        "lost": "lost_skipped",
        "skipped": "lost_skipped",
    }.get(status, "research_qualify")


def grouped_work_opportunities(records: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    records = records if records is not None else load_work_opportunities()
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    grouped = {section: [] for section, _ in WORK_SECTION_ORDER}
    for record in records:
        grouped.setdefault(_section_for_status(str(record.get("status") or "research")), []).append(record)
    for items in grouped.values():
        items.sort(key=lambda item: (priority_rank.get(str(item.get("priority", "medium")), 9), str(item.get("deadline") or "9999-12-31"), -int(item.get("fit_score") or 0)))
    return grouped


def _active_company_blocks() -> dict[str, dict[str, Any]]:
    try:
        return load_company_application_blocks()
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _is_company_block_active(company: str) -> bool:
    block = _active_company_blocks().get(normalize_company_key(company))
    if not block or str(block.get("status", "")).lower() != "blocked":
        return False
    expires_at = _parse_date(block.get("expires_at"))
    return expires_at is None or expires_at >= datetime.now(UTC).date()


def _lane_score(record: dict[str, Any]) -> int:
    text = " ".join(str(record.get(key, "")) for key in ("title", "company", "why_fit", "notes", "next_action", "source_detail")).lower()
    hits = sum(1 for term in TARGET_LANE_TERMS if term in text)
    return min(30, hits * 8)


def _deadline_score(record: dict[str, Any]) -> int:
    deadline = _parse_date(record.get("deadline"))
    if not deadline:
        return 0
    days = (deadline - datetime.now(UTC).date()).days
    if days < 0:
        return -30
    if days <= 2:
        return 25
    if days <= 7:
        return 18
    if days <= 14:
        return 10
    return 3


def _blocked_until_active(record: dict[str, Any]) -> bool:
    blocked_until = _parse_date(record.get("blocked_until"))
    return blocked_until is not None and blocked_until >= datetime.now(UTC).date()


def _work_candidate_score(record: dict[str, Any]) -> int:
    status = str(record.get("status") or "research")
    if status in {"lost", "skipped", "won", "submitted", "applied"}:
        return -1000
    if status == "blocked" or _blocked_until_active(record):
        return -900
    score = int(record.get("fit_score") or 0)
    score += {"high": 35, "medium": 18, "low": 0}.get(str(record.get("priority", "medium")), 0)
    score += {"pursue": 32, "proposal_needed": 30, "qualify": 18, "relationship_strategy": 16, "research": 6}.get(status, 0)
    score += {"high": 28, "medium": 12, "low": 0, "unknown": 4}.get(str(record.get("revenue_potential", "unknown")), 0)
    score += {"high": 22, "medium": 10, "low": 0}.get(str(record.get("relationship_value", "medium")), 0)
    score += _deadline_score(record)
    score += _lane_score(record)
    if str(record.get("opportunity_type")) in {"rfp", "contract_1099", "fractional", "vendor_opportunity", "local_business"}:
        score += 12
    if str(record.get("next_action") or "").strip():
        score += 10
    return score


def _w2_candidate_score(row: dict[str, Any]) -> int:
    status = str(row.get("status") or "")
    application_status = str(row.get("application_status") or "not_applied")
    if application_status in {"applied", "interviewing", "rejected", "offer", "withdrawn", "skipped", "blocked"}:
        return -1000
    if _is_company_block_active(str(row.get("company") or "")):
        return -900
    score = int(row.get("score") or 0)
    score += {"high_fit": 25, "near_fit": 8, "low_fit": -20}.get(str(row.get("classification") or ""), 0)
    score += {"apply_now": 22, "strong_review": 8, "review": 0, "skip": -40}.get(str(row.get("viability_level") or ""), 0)
    score += 12 if str(row.get("geographic_eligibility") or "") == "eligible" else -20
    text = " ".join(str(row.get(key, "")) for key in ("title", "description", "reasons", "role_family")).lower()
    score += min(25, sum(1 for term in TARGET_LANE_TERMS if term in text) * 6)
    return score


def _best_w2_job() -> dict[str, Any] | None:
    jobs = load_scored_jobs()
    if not jobs:
        return None
    ranked = sorted(jobs, key=_w2_candidate_score, reverse=True)
    return ranked[0] if _w2_candidate_score(ranked[0]) > 0 else None


def _company_universe_count() -> int:
    try:
        return len(load_discovered_companies().companies)
    except (OSError, ValueError):
        return 0


def opportunity_review() -> dict[str, Any]:
    """Recommend Cody's highest-leverage next opportunity across products."""
    work = load_work_opportunities()
    best_work = None
    if work:
        ranked_work = sorted(work, key=_work_candidate_score, reverse=True)
        if _work_candidate_score(ranked_work[0]) > 0:
            best_work = ranked_work[0]

    best_w2 = _best_w2_job()
    best_w2_score = _w2_candidate_score(best_w2) if best_w2 else -1000
    best_work_score = _work_candidate_score(best_work) if best_work else -1000

    # Build these to account for durable Opportunity Pipeline and company universe in the reasoning.
    pipeline_records = build_opportunity_pipeline(persist=True)
    universe_count = _company_universe_count()
    block_count = len(_active_company_blocks())

    if best_work and best_work_score >= best_w2_score:
        return {
            "best_next_action_today": best_work.get("next_action") or f"Advance {best_work.get('title')} with {best_work.get('company')}.",
            "recommended_opportunity_type": best_work.get("opportunity_type"),
            "recommended_company": best_work.get("company"),
            "recommended_opportunity_id": best_work.get("opportunity_id"),
            "recommended_action": best_work.get("next_action") or "Advance qualification, outreach, or proposal prep today.",
            "reasoning": (
                "Highest-leverage available work opportunity beats the best W2 option after weighing target-lane fit, "
                "actionability today, deadline urgency, relationship value, revenue/career upside, and blocks. "
                f"Compared {len(work)} work opportunities, {len(load_scored_jobs())} W2 jobs, {len(pipeline_records)} pipeline companies, "
                f"{universe_count} company-universe records, and {block_count} blocked-company records."
            ),
        }

    if best_w2:
        return {
            "best_next_action_today": f"Apply or prepare for {best_w2.get('company')} {best_w2.get('title')} after final manual review.",
            "recommended_opportunity_type": "w2_job",
            "recommended_company": best_w2.get("company"),
            "recommended_opportunity_id": best_w2.get("id"),
            "recommended_action": "Prepare or submit the strongest actionable W2 role today.",
            "reasoning": (
                "The best W2 job is more actionable than current non-W2 opportunities after checking fit score, geography, "
                "viability, application status, company blocks, and target-lane overlap. "
                f"Compared {len(work)} work opportunities, {len(load_scored_jobs())} W2 jobs, {len(pipeline_records)} pipeline companies, "
                f"{universe_count} company-universe records, and {block_count} blocked-company records."
            ),
        }

    return {
        "best_next_action_today": "Research and add one high-leverage AI agent deployment, RFP, local-business, vendor, or warm-intro opportunity.",
        "recommended_opportunity_type": "manual_lead",
        "recommended_company": None,
        "recommended_opportunity_id": None,
        "recommended_action": "Add or qualify a new non-W2 opportunity today.",
        "reasoning": (
            "No actionable W2 or durable work opportunity is ready today; expand the Work Opportunity Engine rather than forcing weak W2 activity. "
            f"Compared {len(work)} work opportunities, {len(load_scored_jobs())} W2 jobs, {len(pipeline_records)} pipeline companies, "
            f"{universe_count} company-universe records, and {block_count} blocked-company records."
        ),
    }


def get_work_opportunity(opportunity_id: str) -> dict[str, Any]:
    for record in load_work_opportunities():
        if str(record.get("opportunity_id")) == opportunity_id:
            return record
    raise ValueError(f"Work opportunity not found: {opportunity_id}")


def prep_work_opportunity(opportunity_id: str, prep_kind: str) -> dict[str, Any]:
    """Create markdown prep files for an RFP, 1099, or local outreach opportunity."""
    record = get_work_opportunity(opportunity_id)
    folder = Path("applications") / "work_opportunities" / f"{_slugify(str(record.get('company') or 'company'))}_{_slugify(str(record.get('title') or 'opportunity'))}_{opportunity_id}"
    folder.mkdir(parents=True, exist_ok=True)
    context = {
        "kind": prep_kind,
        "title": record.get("title", ""),
        "company": record.get("company", ""),
        "opportunity_type": record.get("opportunity_type", ""),
        "source": record.get("source", ""),
        "url": record.get("url", ""),
        "deadline": record.get("deadline", ""),
        "why_fit": record.get("why_fit", ""),
        "next_action": record.get("next_action", ""),
        "risks": record.get("risks", ""),
        "notes": record.get("notes", ""),
    }
    file_bodies = {
        "opportunity_brief.md": f"# Opportunity brief\n\n- Type: {context['opportunity_type']}\n- Company/organization: {context['company']}\n- Title: {context['title']}\n- Source: {context['source']}\n- URL: {context['url']}\n- Deadline: {context['deadline']}\n\n## Why fit\n{context['why_fit']}\n",
        "qualification_checklist.md": "# Qualification checklist\n\n- [ ] Confirm business problem and decision maker.\n- [ ] Confirm Cody's AI agent deployment lane fit.\n- [ ] Confirm budget, timing, and procurement path.\n- [ ] Confirm next concrete action Cody can take today.\n",
        "proposed_solution_outline.md": f"# Proposed solution outline\n\nFrame a {prep_kind} solution around AI agents, workflow automation, AI operations, product systems, technical implementation, analytics, and measurable business-process improvement.\n",
        "risks.md": f"# Risks\n\n{context['risks'] or 'Add delivery, procurement, relationship, deadline, and scope risks before submitting or outreaching.'}\n",
        "outreach_note.md": f"# Outreach note\n\nDraft a concise note for {context['company']} about {context['title']} and the target workflow or AI operations problem Cody can help diagnose.\n",
        "next_steps.md": f"# Next steps\n\n{context['next_action'] or 'Define the next action, owner, and deadline.'}\n",
    }
    for filename, body in file_bodies.items():
        (folder / filename).write_text(body, encoding="utf-8")
    return {"opportunity_id": opportunity_id, "prep_kind": prep_kind, "folder": str(folder), "files": [str(folder / filename) for filename in PREP_FILES]}
