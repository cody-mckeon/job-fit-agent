"""Durable Work Opportunity Engine for W2, 1099, RFP, vendor, and manual leads."""

from __future__ import annotations

import csv
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
    "relationship",
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
    "source_file",
    "search_seed",
    "rfp_portal",
    "vendor_portal",
    "relationship_map",
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

PREP_FILE_SETS = {
    "rfp": (
        "rfp_summary.md",
        "go_no_go_checklist.md",
        "required_documents.md",
        "proposal_outline.md",
        "questions_to_ask.md",
        "risks.md",
        "next_steps.md",
        # Backward-compatible general prep artifacts.
        "opportunity_brief.md",
        "proposed_solution_outline.md",
    ),
    "1099": (
        "opportunity_brief.md",
        "qualification_checklist.md",
        "scope_hypothesis.md",
        "pricing_hypothesis.md",
        "outreach_note.md",
        "risks.md",
        "next_steps.md",
        # Backward-compatible general prep artifact.
        "proposed_solution_outline.md",
    ),
    "local_outreach": (
        "business_profile.md",
        "pain_hypothesis.md",
        "ai_pilot_idea.md",
        "diagnostic_offer.md",
        "outreach_note.md",
        "follow_up_sequence.md",
        # Risk capture remains useful for local outreach and preserves the prior CLI contract.
        "risks.md",
    ),
}


DISCOVERY_COMMANDS = {
    "discover-w2": "w2_job",
    "discover-contracts": "contract_1099",
    "discover-rfps": "rfp",
    "discover-local-businesses": "local_business",
    "discover-relationships": "relationship",
}

LANE_DEFAULTS = {
    "contract_1099": {
        "status": "qualify",
        "next_action": "qualify buyer, scope, budget, timeline, and delivery risk",
    },
    "fractional": {
        "status": "qualify",
        "next_action": "qualify buyer, scope, budget, timeline, and delivery risk",
    },
    "vendor_opportunity": {
        "status": "qualify",
        "next_action": "qualify buyer, scope, budget, timeline, and delivery risk",
    },
    "rfp": {
        "status": "qualify",
        "deadline_status": "proposal_needed",
        "next_action": "run go/no-go checklist and review submission requirements",
    },
    "local_business": {
        "status": "qualify",
        "next_action": "research pain points and prepare diagnostic outreach",
    },
    "relationship": {
        "status": "relationship_strategy",
        "next_action": "draft outreach and define reason to connect",
    },
}

LANE_QUALIFICATION_RULES = {
    "w2_job": ("role fit", "geography", "company block status", "application channel"),
    "contract_1099": ("clear business problem", "short implementation cycle", "AI/workflow automation fit", "buyer reachable", "delivery risk"),
    "fractional": ("clear business problem", "short implementation cycle", "AI/workflow automation fit", "buyer reachable", "delivery risk"),
    "rfp": ("deadline", "eligibility", "required documents", "scope fit", "proposal complexity", "go/no-go"),
    "vendor_opportunity": ("clear business problem", "short implementation cycle", "AI/workflow automation fit", "buyer reachable", "delivery risk"),
    "local_business": ("likely workflow pain", "local relevance", "reachable decision maker", "simple pilot opportunity"),
    "relationship": ("relevance", "warmth", "reason to reach out", "potential opportunity path"),
}


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


def _dedupe_key(title: str, company: str, opportunity_type: str, url: str = "") -> str:
    """Return a stable identity key for discovered or manually-added opportunities."""
    url_key = str(url or "").strip().lower()
    if url_key:
        return f"url:{url_key}"
    digest = hashlib.sha1(f"{opportunity_type}|{company.strip().lower()}|{title.strip().lower()}".encode()).hexdigest()[:16]
    return f"opp:{digest}"


def _opportunity_id(title: str, company: str, opportunity_type: str, source: str, created_at: str) -> str:
    digest = hashlib.sha1(f"{title}|{company}|{opportunity_type}|{source}|{created_at}".encode()).hexdigest()[:12]
    return f"work-{digest}"


def _bounded_int(value: Any, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(100, parsed))


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
    if opportunity_type == "warm_intro":
        opportunity_type = "relationship"
    url = str(record.get("url") or "")
    recommended_next_action = str(record.get("recommended_next_action") or record.get("next_action") or "")
    actionability_score = _bounded_int(record.get("actionability_score"), 0)
    urgency_score = _bounded_int(record.get("urgency_score"), 0)
    return {
        "opportunity_id": str(record.get("opportunity_id") or _opportunity_id(title, company, opportunity_type, source, created_at)),
        "dedupe_key": str(record.get("dedupe_key") or _dedupe_key(title, company, opportunity_type, url)),
        "title": title,
        "company": company,
        "opportunity_type": opportunity_type,
        "source": source,
        "source_detail": str(record.get("source_detail") or ""),
        "url": url,
        "status": str(record.get("status") or "research").strip().lower() if str(record.get("status") or "research").strip().lower() in VALID_WORK_STATUSES else "research",
        "priority": str(record.get("priority") or "medium").strip().lower() if str(record.get("priority") or "medium").strip().lower() in VALID_WORK_PRIORITIES else "medium",
        "fit_score": _bounded_int(record.get("fit_score"), 0),
        "actionability_score": actionability_score,
        "urgency_score": urgency_score,
        "revenue_potential": str(record.get("revenue_potential") or "unknown").strip().lower() if str(record.get("revenue_potential") or "unknown").strip().lower() in VALID_REVENUE_POTENTIALS else "unknown",
        "relationship_value": str(record.get("relationship_value") or "medium").strip().lower() if str(record.get("relationship_value") or "medium").strip().lower() in VALID_RELATIONSHIP_VALUES else "medium",
        "deadline": str(record.get("deadline") or ""),
        "blocked_until": str(record.get("blocked_until") or ""),
        "next_action": recommended_next_action,
        "recommended_next_action": recommended_next_action,
        "qualification": record.get("qualification") if isinstance(record.get("qualification"), dict) else {},
        "why_fit": str(record.get("why_fit") or ""),
        "risks": str(record.get("risks") or ""),
        "notes": str(record.get("notes") or ""),
        "created_at": created_at,
        "updated_at": str(record.get("updated_at") or now),
    }


def _lane_default_status(opportunity_type: str, deadline: Any = "") -> str:
    defaults = LANE_DEFAULTS.get(opportunity_type, {})
    if opportunity_type == "rfp" and str(deadline or "").strip():
        return str(defaults.get("deadline_status") or defaults.get("status") or "qualify")
    return str(defaults.get("status") or "research")


def _lane_default_next_action(opportunity_type: str) -> str:
    return str(LANE_DEFAULTS.get(opportunity_type, {}).get("next_action") or "")


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
    status = _validate_choice("status", str(kwargs.get("status") or _lane_default_status(opportunity_type, kwargs.get("deadline"))), VALID_WORK_STATUSES)
    priority = _validate_choice("priority", str(kwargs.get("priority") or "medium"), VALID_WORK_PRIORITIES)
    normalized_input = {**kwargs, "title": title, "company": company, "opportunity_type": opportunity_type}
    qualification = kwargs.get("qualification")
    if not isinstance(qualification, dict) or not qualification:
        qualification = _qualification_from_record(opportunity_type, normalized_input)
    scores = _score_discovered_record(opportunity_type, normalized_input, qualification)
    now = utc_timestamp()
    next_action = str(kwargs.get("recommended_next_action") or kwargs.get("next_action") or _lane_default_next_action(opportunity_type))
    record = normalize_work_opportunity({
        **kwargs,
        **{key: kwargs.get(key, value) for key, value in scores.items()},
        "title": title,
        "company": company,
        "opportunity_type": opportunity_type,
        "source": source,
        "status": status,
        "priority": priority,
        "next_action": next_action,
        "recommended_next_action": next_action,
        "qualification": qualification,
        "created_at": now,
        "updated_at": now,
    })
    records = load_work_opportunities()
    existing_idx = _find_existing_opportunity_index(records, record)
    if existing_idx is None:
        records.append(record)
    else:
        existing = records[existing_idx]
        record["opportunity_id"] = existing.get("opportunity_id", record["opportunity_id"])
        record["created_at"] = existing.get("created_at", record["created_at"])
        records[existing_idx] = normalize_work_opportunity({**existing, **record, "updated_at": now})
        record = records[existing_idx]
    save_work_opportunities(records)
    return record


def _find_existing_opportunity_index(records: list[dict[str, Any]], record: dict[str, Any]) -> int | None:
    key = str(record.get("dedupe_key") or "")
    for idx, existing in enumerate(records):
        if key and str(existing.get("dedupe_key") or "") == key:
            return idx
        if record.get("url") and str(existing.get("url") or "").strip().lower() == str(record.get("url") or "").strip().lower():
            return idx
        if (
            str(existing.get("opportunity_type") or "") == str(record.get("opportunity_type") or "")
            and str(existing.get("company") or "").strip().lower() == str(record.get("company") or "").strip().lower()
            and str(existing.get("title") or "").strip().lower() == str(record.get("title") or "").strip().lower()
        ):
            return idx
    return None


def add_rfp(**kwargs: Any) -> dict[str, Any]:
    """Create an RFP work opportunity."""
    deadline = kwargs.get("deadline") or ""
    status = str(kwargs.get("status") or _lane_default_status("rfp", deadline))
    return add_work_opportunity(
        title=kwargs.get("title"),
        company=kwargs.get("organization") or kwargs.get("company"),
        opportunity_type="rfp",
        source=kwargs.get("source") or "government",
        source_detail=kwargs.get("source_detail") or "",
        url=kwargs.get("url") or "",
        deadline=deadline,
        priority=kwargs.get("priority") or "medium",
        status=status,
        why_fit=kwargs.get("why_fit") or "",
        notes=kwargs.get("notes") or "",
        next_action=kwargs.get("next_action") or _lane_default_next_action("rfp"),
        required_documents=kwargs.get("required_documents") or kwargs.get("documents") or [],
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
    score += int(record.get("actionability_score") or 0) // 2
    score += int(record.get("urgency_score") or 0) // 2
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
    qualification = record.get("qualification") if isinstance(record.get("qualification"), dict) else {}
    required_docs = qualification.get("required_documents") or []
    if isinstance(required_docs, str):
        required_docs = [required_docs]
    docs_body = "\n".join(f"- [ ] {doc}" for doc in required_docs) or "- [ ] Proposal narrative\n- [ ] Pricing / budget\n- [ ] Eligibility or vendor forms\n- [ ] Submission portal/account requirements"
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
    common_brief = f"- Type: {context['opportunity_type']}\n- Company/organization: {context['company']}\n- Title: {context['title']}\n- Source: {context['source']}\n- URL: {context['url']}\n- Deadline: {context['deadline']}\n\n## Why fit\n{context['why_fit']}\n"
    file_bodies = {
        "rfp_summary.md": f"# RFP summary\n\n{common_brief}\n## Submission posture\nRun a go/no-go review before drafting and confirm eligibility, required documents, proposal complexity, scope fit, and deadline.\n",
        "go_no_go_checklist.md": "# Go/no-go checklist\n\n- [ ] Deadline leaves enough time to submit a quality response.\n- [ ] Cody is eligible to submit or can partner with an eligible vendor.\n- [ ] Scope matches AI agents, workflow automation, AI operations, product systems, or product analytics.\n- [ ] Required documents are feasible.\n- [ ] Proposal complexity is proportional to likely revenue and relationship value.\n",
        "required_documents.md": f"# Required documents\n\n{docs_body}\n",
        "proposal_outline.md": "# Proposal outline\n\n1. Executive summary\n2. Understanding of workflow pain and desired outcome\n3. AI agent / automation implementation approach\n4. Delivery plan and timeline\n5. Relevant experience and operating model\n6. Pricing / assumptions\n7. Risks, dependencies, and questions\n",
        "questions_to_ask.md": "# Questions to ask\n\n- What workflow or operational bottleneck matters most?\n- Who owns implementation and acceptance?\n- What systems, data, and access are in scope?\n- Are vendor eligibility, insurance, or security requirements negotiable?\n",
        "opportunity_brief.md": f"# Opportunity brief\n\n{common_brief}",
        "qualification_checklist.md": "# Qualification checklist\n\n- [ ] Confirm workflow pain and business owner.\n- [ ] Confirm Cody's AI agent deployment lane fit.\n- [ ] Confirm budget, timing, and procurement path.\n- [ ] Confirm buyer reachability and delivery risk.\n- [ ] Confirm next concrete action Cody can take today.\n",
        "proposed_solution_outline.md": f"# Proposed solution outline\n\nFrame a {prep_kind} solution around AI agents, workflow automation, AI operations, product systems, technical implementation, analytics, and measurable business-process improvement.\n",
        "scope_hypothesis.md": "# Scope hypothesis\n\nDraft a small, concrete implementation scope: target workflow, users, systems, data inputs, agent/automation behavior, success metric, and out-of-scope items.\n",
        "pricing_hypothesis.md": "# Pricing hypothesis\n\nDefine likely pilot, fixed-scope, or advisory pricing with assumptions about timeline, access, implementation risk, and expected revenue potential.\n",
        "business_profile.md": f"# Business profile\n\n{common_brief}\n## Local relevance\nSummarize location, decision maker, operations model, current systems, and why this business is likely to have workflow pain.\n",
        "pain_hypothesis.md": "# Pain hypothesis\n\nList likely manual workflows, reporting gaps, scheduling/intake bottlenecks, customer follow-up misses, or operational handoffs that AI agents could improve.\n",
        "ai_pilot_idea.md": "# AI pilot idea\n\nPropose one simple pilot with a clear user, trigger, workflow, data source, output, review step, and success metric.\n",
        "diagnostic_offer.md": "# Diagnostic offer\n\nOffer a short diagnostic conversation and workflow teardown that identifies one automation pilot Cody can scope quickly.\n",
        "outreach_note.md": f"# Outreach note\n\nDraft a concise note for {context['company']} about {context['title']} and the target workflow or AI operations problem Cody can help diagnose.\n",
        "follow_up_sequence.md": "# Follow-up sequence\n\n1. Initial diagnostic offer\n2. Value-add follow-up with a concrete workflow hypothesis\n3. Short proof-of-concept suggestion\n4. Close-loop note asking for the right decision maker\n",
        "risks.md": f"# Risks\n\n{context['risks'] or 'Add delivery, procurement, relationship, deadline, scope, data-access, and buyer-availability risks before submitting or outreaching.'}\n",
        "next_steps.md": f"# Next steps\n\n{context['next_action'] or 'Define the next action, owner, and deadline.'}\n",
    }
    filenames = PREP_FILE_SETS.get(prep_kind, PREP_FILE_SETS["1099"])
    for filename in filenames:
        (folder / filename).write_text(file_bodies[filename], encoding="utf-8")
    return {"opportunity_id": opportunity_id, "prep_kind": prep_kind, "folder": str(folder), "files": [str(folder / filename) for filename in filenames]}


def _record_from_text_line(line: str) -> dict[str, Any] | None:
    text = line.strip().lstrip("-*").strip()
    if not text or text.startswith("#"):
        return None
    url_match = re.search(r"https?://\S+", text)
    url = url_match.group(0).rstrip(").,;") if url_match else ""
    if url:
        text = text.replace(url_match.group(0), "").strip(" -—|,;")
    parts = [part.strip() for part in re.split(r"\s+(?:[-—|]|::)\s+", text) if part.strip()]
    if len(parts) >= 2:
        company, title = parts[0], parts[1]
        description = " - ".join(parts[2:])
    else:
        company, title, description = "Unknown organization", text, ""
    return {"company": company, "title": title, "description": description, "url": url, "source": "source_file"}


def _load_discovery_source_file(source_file: str | Path) -> list[dict[str, Any]]:
    """Load discovery seed records from CSV, JSON, JSONL, YAML, Markdown, or plain text."""
    path = Path(source_file)
    if not path.exists():
        raise ValueError(f"Source file not found: {source_file}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix in {".jsonl", ".ndjson"}:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    if suffix in {".md", ".markdown", ".txt", ".text"}:
        return [record for line in text.splitlines() if (record := _record_from_text_line(line))]
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is present in this project
            raise ValueError("YAML source files require PyYAML.") from exc
        payload = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("opportunities") or payload.get("items") or payload.get("records") or [payload]
    if not isinstance(payload, list):
        raise ValueError("Discovery source file must contain a list, or a dict with opportunities/items/records.")
    return [item for item in payload if isinstance(item, dict)]


def _text_has_any(text: str, terms: tuple[str, ...]) -> bool:
    normalized = text.lower()
    return any(term in normalized for term in terms)


def _qualification_from_record(lane: str, record: dict[str, Any], query: str = "", location: str = "") -> dict[str, Any]:
    text = " ".join(str(record.get(key, "")) for key in ("title", "company", "description", "why_fit", "notes", "source_detail", "application_channel", "documents", "contact", "url"))
    deadline = str(record.get("deadline") or record.get("due_date") or "")
    company = str(record.get("company") or record.get("organization") or record.get("business") or "")
    qualification: dict[str, Any] = {"rules": list(LANE_QUALIFICATION_RULES.get(lane, ())), "lane": lane}
    if lane == "w2_job":
        qualification.update({
            "role_fit": _text_has_any(text, TARGET_LANE_TERMS + ("product manager", "program manager", "solutions", "implementation")),
            "geography": bool(record.get("location") or location or "remote" in text.lower()),
            "company_block_status": "blocked" if _is_company_block_active(company) else "clear",
            "application_channel": str(record.get("application_channel") or record.get("source") or record.get("url") or "unknown"),
        })
    elif lane in {"contract_1099", "fractional", "vendor_opportunity"}:
        qualification.update({
            "clear_business_problem": _text_has_any(text, ("problem", "pain", "need", "improve", "automate", "workflow", "operations", "process")),
            "short_implementation_cycle": _text_has_any(text, ("pilot", "quick", "30 day", "60 day", "sprint", "short", "implementation")),
            "ai_workflow_automation_fit": _text_has_any(text, TARGET_LANE_TERMS),
            "buyer_reachable": bool(record.get("contact") or record.get("email") or record.get("buyer") or record.get("url")),
            "delivery_risk": str(record.get("delivery_risk") or record.get("risk") or "medium"),
        })
    elif lane == "rfp":
        days = None
        parsed_deadline = _parse_date(deadline)
        if parsed_deadline:
            days = (parsed_deadline - datetime.now(UTC).date()).days
        complexity = str(record.get("proposal_complexity") or ("high" if _text_has_any(text, ("bond", "insurance", "certification", "security", "multi-year")) else "medium"))
        qualification.update({
            "deadline": deadline,
            "days_until_deadline": days,
            "eligibility": str(record.get("eligibility") or "needs_review"),
            "required_documents": record.get("required_documents") or record.get("documents") or [],
            "scope_fit": _text_has_any(text, TARGET_LANE_TERMS),
            "proposal_complexity": complexity,
            "go_no_go": "go" if parsed_deadline and days is not None and days >= 0 and complexity != "high" else "review",
        })
    elif lane == "local_business":
        qualification.update({
            "likely_workflow_pain": _text_has_any(text, ("manual", "workflow", "operations", "scheduling", "intake", "reporting", "spreadsheet", "automation", "process")),
            "local_relevance": bool(location and location.lower() in text.lower()) or bool(record.get("location")),
            "reachable_decision_maker": bool(record.get("owner") or record.get("contact") or record.get("email") or record.get("url")),
            "simple_pilot_opportunity": _text_has_any(text, ("pilot", "quick", "simple", "workflow", "intake", "reporting", "automation")),
        })
    elif lane == "relationship":
        qualification.update({
            "relevance": _text_has_any(text, TARGET_LANE_TERMS) or bool(query and query.lower() in text.lower()),
            "warmth": str(record.get("warmth") or record.get("relationship_warmth") or "medium"),
            "reason_to_reach_out": str(record.get("reason_to_reach_out") or record.get("reason") or query or "Share a relevant update and ask for perspective."),
            "potential_opportunity_path": str(record.get("potential_opportunity_path") or record.get("opportunity_path") or "Explore intro, advice, contract, vendor, or W2 path."),
        })
    return qualification


def _score_discovered_record(lane: str, record: dict[str, Any], qualification: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(str(record.get(key, "")) for key in ("title", "company", "description", "why_fit", "notes", "source_detail"))
    fit_score = _bounded_int(record.get("fit_score"), 35 + min(45, sum(1 for term in TARGET_LANE_TERMS if term in text.lower()) * 8))
    actionability_score = _bounded_int(record.get("actionability_score"), 45)
    urgency_score = _bounded_int(record.get("urgency_score"), 20)
    revenue_potential = str(record.get("revenue_potential") or "medium").lower()
    relationship_value = str(record.get("relationship_value") or "medium").lower()

    if lane == "w2_job":
        actionability_score = 75 if qualification.get("application_channel") != "unknown" and qualification.get("company_block_status") == "clear" else 25
        revenue_potential = record.get("revenue_potential") or "medium"
    elif lane in {"contract_1099", "fractional", "vendor_opportunity"}:
        actionability_score = 70 if qualification.get("buyer_reachable") else 45
        revenue_potential = record.get("revenue_potential") or "high"
    elif lane == "rfp":
        days = qualification.get("days_until_deadline")
        if isinstance(days, int):
            urgency_score = 95 if 0 <= days <= 2 else 82 if days <= 7 else 55 if days <= 14 else 25
        actionability_score = 70 if qualification.get("go_no_go") == "go" else 45
        revenue_potential = record.get("revenue_potential") or "high"
    elif lane == "local_business":
        actionability_score = 75 if qualification.get("reachable_decision_maker") else 50
        revenue_potential = record.get("revenue_potential") or "medium"
    elif lane == "relationship":
        actionability_score = 70 if qualification.get("reason_to_reach_out") else 40
        relationship_value = record.get("relationship_value") or "high"
        revenue_potential = record.get("revenue_potential") or "unknown"

    return {
        "fit_score": fit_score,
        "actionability_score": actionability_score,
        "urgency_score": urgency_score,
        "revenue_potential": revenue_potential if revenue_potential in VALID_REVENUE_POTENTIALS else "medium",
        "relationship_value": relationship_value if relationship_value in VALID_RELATIONSHIP_VALUES else "medium",
    }


def _default_next_action(lane: str, company: str, title: str, qualification: dict[str, Any]) -> str:
    if lane == "w2_job":
        if qualification.get("company_block_status") == "blocked":
            return f"Use {company} as a relationship strategy; do not submit a W2 application while the company block is active."
        return f"Confirm geography and application channel, then prepare the W2 application for {title}."
    if lane in LANE_DEFAULTS:
        return _lane_default_next_action(lane)
    return f"Qualify {title} with {company}."


def normalize_discovered_opportunity(raw: dict[str, Any], lane: str, query: str = "", location: str = "") -> dict[str, Any]:
    """Normalize one discovery hit into the Work Opportunity Engine schema."""
    title = str(raw.get("title") or raw.get("name") or raw.get("role") or raw.get("opportunity") or query or f"{lane.replace('_', ' ').title()} opportunity").strip()
    company = str(raw.get("company") or raw.get("organization") or raw.get("business") or raw.get("account") or raw.get("person") or "Unknown organization").strip()
    source = str(raw.get("source") or "source_file").strip().lower()
    if source not in VALID_WORK_SOURCES:
        source = "other"
    opportunity_type = lane
    qualification = _qualification_from_record(lane, {**raw, "title": title, "company": company}, query=query, location=location)
    status = str(raw.get("status") or "qualify").lower()
    if lane == "rfp":
        status = str(raw.get("status") or "proposal_needed").lower()
    if lane == "relationship":
        status = str(raw.get("status") or "relationship_strategy").lower()
    if lane == "w2_job" and qualification.get("company_block_status") == "blocked":
        opportunity_type = "relationship"
        status = "relationship_strategy"
        qualification["converted_from_blocked_w2"] = True
    scores = _score_discovered_record(lane, raw, qualification)
    default_action_lane = lane if qualification.get("converted_from_blocked_w2") else opportunity_type
    next_action = str(raw.get("recommended_next_action") or raw.get("next_action") or _default_next_action(default_action_lane, company, title, qualification))
    description = str(raw.get("description") or raw.get("summary") or "")
    why_fit = str(raw.get("why_fit") or description or query or "Discovered through structured swim-lane discovery.")
    return normalize_work_opportunity({
        **raw,
        "title": title,
        "company": company,
        "opportunity_type": opportunity_type,
        "source": source,
        "source_detail": str(raw.get("source_detail") or query or location or "structured discovery"),
        "url": str(raw.get("url") or raw.get("link") or ""),
        "deadline": str(raw.get("deadline") or raw.get("due_date") or ""),
        "status": status if status in VALID_WORK_STATUSES else "qualify",
        "priority": str(raw.get("priority") or ("high" if scores["urgency_score"] >= 80 or scores["fit_score"] >= 70 else "medium")),
        "recommended_next_action": next_action,
        "next_action": next_action,
        "why_fit": why_fit,
        "notes": str(raw.get("notes") or ""),
        "qualification": qualification,
        **scores,
    })


def discover_opportunities(lane: str, source_file: str | Path | None = None, query: str = "", location: str = "", limit: int = 25) -> dict[str, Any]:
    """Import or seed discovered opportunities for one swim lane."""
    if lane not in VALID_OPPORTUNITY_TYPES or lane == "manual_lead":
        raise ValueError(f"Invalid discovery lane: {lane}")
    raw_items = _load_discovery_source_file(source_file) if source_file else []
    if not raw_items and (query or location):
        raw_items = [{"title": query or f"{lane.replace('_', ' ').title()} discovery seed", "company": location or "Discovery seed", "source": "search_seed", "source_detail": query, "location": location}]
    selected = raw_items[: max(0, limit)]
    records = load_work_opportunities()
    created = 0
    updated = 0
    normalized: list[dict[str, Any]] = []
    now = utc_timestamp()
    for item in selected:
        candidate = normalize_discovered_opportunity(item, lane, query=query, location=location)
        existing_idx = _find_existing_opportunity_index(records, candidate)
        if existing_idx is None:
            created += 1
            records.append(candidate)
            normalized.append(candidate)
            continue
        existing = records[existing_idx]
        merged = normalize_work_opportunity({**existing, **candidate, "opportunity_id": existing.get("opportunity_id"), "created_at": existing.get("created_at"), "updated_at": now})
        records[existing_idx] = merged
        updated += 1
        normalized.append(merged)
    save_work_opportunities(records)
    return {"lane": lane, "created": created, "updated": updated, "count": len(normalized), "opportunities": normalized}
