"""Keyword-based scoring for job fit."""

from __future__ import annotations

import re

from job_fit_agent.config import TargetProfile
from job_fit_agent.models import FitScore, JobPosting

BASE_TITLE_SCORE = 26
BASE_KEYWORD_SCORE = 8
PREFERRED_LOCATION_SCORE = 12
EXCLUDED_LOCATION_PENALTY = -35
LOCATION_NOT_FIT_CAP = 44
REMOTE_US_BONUS = 16
LOCAL_LOCATION_BONUS = 14
HYBRID_LOCAL_BONUS = 10
US_NON_LOCAL_PENALTY = -40
ONSITE_INTERNATIONAL_PENALTY = -60
UNKNOWN_LOCATION_PENALTY = -4
HYBRID_UNSPECIFIED_PENALTY = -8
PRIORITY_COMPANY_BONUS = 10
INDUSTRY_BIAS_BONUS = 5
INDUSTRY_BIAS_BONUS_CAP = 20
LOCAL_PRIORITY_COMPANY_BONUS = 12

NEGATIVE_KEYWORDS = {
    "engineer only": -30,
    "software engineer": -35,
    "infrastructure engineer": -35,
    "nurse": -45,
    "driver": -45,
    "sales development": -35,
    "account executive": -35,
    "finance operations": -20,
    "tax": -25,
    "legal": -25,
    "government relations": -25,
    "public policy": -20,
    "treasury": -35,
    "customer support only": -30,
    "onsite outside us": -25,
}

NEAR_FIT_TERMS = {
    "product marketing manager",
    "technical program manager",
    "engineering program manager",
    "growth marketing",
    "demand generation",
    "technical account manager",
    "customer success analytics",
}

PMM_HIGH_FIT_KEYWORDS = {"product analytics", "experimentation", "ai", "platform", "customer-facing web"}
FORCED_LOW_FIT_TITLES = {
    "software engineer",
    "member of technical staff",
    "infrastructure engineer",
    "engineering manager",
}
AI_BUILDER_KEYWORDS = {
    "agents",
    "agentic",
    "workflows",
    "orchestration",
    "automation",
    "copilots",
    "llm",
    "ai tooling",
    "internal tools",
    "developer tools",
    "rapid prototyping",
    "ai operations",
    "prompt systems",
    "workflow systems",
    "ai platform",
    "ai-native",
    "operational ai",
    "integrations",
    "apis",
    "openai",
    "anthropic",
    "langchain",
    "mcp",
    "rag",
}
AI_BUILDER_KEYWORD_BONUS = 6
AI_BUILDER_BONUS_CAP = 30
NEGATIVE_ENGINEERING_KEYWORDS = {
    "distributed systems",
    "compiler",
    "kernel",
    "c++",
    "rust",
    "low latency systems",
    "networking stack",
    "infrastructure reliability",
    "kubernetes",
    "sre",
    "firmware",
}

NON_LOCAL_HYBRID_CITY_TERMS = {
    "foster city, ca",
    "san francisco, ca",
    "new york, ny",
    "sf",
    "ny",
}

LOCAL_LOCATION_TERMS = ("las vegas", "henderson", "nevada", " nv", "remote us", "us remote", "remote united states")
LOCAL_GEOGRAPHY_TERMS = ("las vegas", "henderson", "nevada")
REMOTE_US_TERMS = ("remote us", "us remote", "remote united states", "united states", "usa")
REMOTE_NON_US_TERMS = ("mexico", "argentina", "peru", "latam", "emea", "apac", "canada only", "canada", "united kingdom", "uk", "england", "london", "australia", "anz", "japan", "europe", "western europe")
NON_LOCAL_HYBRID_TERMS = ("foster city", "san francisco", "new york", "nyc", "seattle", "toronto")

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA",
    "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}
REMOTE_US_ALIASES = ("us remote", "remote us", "remote usa", "remote united states", "united states", "anywhere in us", "usa")
NON_US_REGIONS = (
    "latam",
    "emea",
    "apac",
    "europe",
    "western europe",
    "japan",
    "mexico",
    "argentina",
    "peru",
    "united kingdom",
    "uk",
    "england",
    "london",
    "australia",
    "anz",
    "canada only",
)
REVIEW_REGIONS = ("north america",)


def normalize_location(location_raw: str, workplace_type: str) -> dict[str, str]:
    location = (location_raw or "").strip()
    workplace = (workplace_type or "").strip().lower()
    combined = f"{location.lower()} {workplace}".strip()
    normalized_location_type = "unknown"
    if "hybrid" in combined:
        normalized_location_type = "hybrid"
    elif "onsite" in combined or "on-site" in combined:
        normalized_location_type = "onsite"
    elif "remote" in combined:
        normalized_location_type = "remote"

    normalized_country = ""
    normalized_state = ""
    normalized_city = ""
    geographic_eligibility = "review"

    if any(alias in combined for alias in REMOTE_US_ALIASES):
        normalized_country = "US"
    has_multi_country = bool(re.search(r"\b(canada|mexico|argentina|peru)\b", combined))
    if has_multi_country:
        if re.search(r"\b(us|usa|united states)\b", combined):
            geographic_eligibility = "review"
        else:
            geographic_eligibility = "ineligible"
    if any(region in combined for region in NON_US_REGIONS):
        geographic_eligibility = "ineligible"
    if any(region in combined for region in REVIEW_REGIONS):
        geographic_eligibility = "review"
    if normalized_location_type == "remote" and normalized_country == "US" and not has_multi_country:
        geographic_eligibility = "eligible"
    elif normalized_location_type == "remote" and not location:
        geographic_eligibility = "review"
    elif not location:
        geographic_eligibility = "review"

    city_state_match = re.search(r"^\s*([^,;]+),\s*([A-Za-z]{2})\b", location)
    if city_state_match:
        normalized_city = city_state_match.group(1).strip()
        state = city_state_match.group(2).upper()
        if state in US_STATE_CODES:
            normalized_state = state
            normalized_country = "US"
            if normalized_location_type == "hybrid":
                geographic_eligibility = "eligible" if state == "NV" else "ineligible"

    return {
        "location_raw": location_raw or "",
        "normalized_country": normalized_country,
        "normalized_state": normalized_state,
        "normalized_city": normalized_city,
        "normalized_location_type": normalized_location_type,
        "geographic_eligibility": geographic_eligibility,
    }


def evaluate_location_viability(location: str, workplace_type: str) -> tuple[str, list[str]]:
    """Evaluate explicit geographic viability for Cody."""
    normalized = normalize_location(location, workplace_type)
    eligibility = normalized["geographic_eligibility"]
    location_type = normalized["normalized_location_type"]
    state = normalized["normalized_state"]
    location_text = (location or "").lower()

    if eligibility == "eligible":
        if location_type == "remote":
            return "apply_now", ["Remote US role matches target geography"]
        return "apply_now", ["Location aligns with target geography"]
    if eligibility == "ineligible":
        if "europe" in location_text or "western europe" in location_text:
            return "skip", ["Remote role restricted to Europe"]
        if any(term in location_text for term in ("united kingdom", " uk", "england", "london")):
            return "skip", ["Remote role restricted to United Kingdom", "Remote role limited to non-US geography"]
        if "australia" in location_text or "anz" in location_text:
            return "skip", ["Remote role restricted to Australia / ANZ", "Remote role limited to non-US geography"]
        if "latam" in location_text:
            return "skip", ["Remote role restricted to LATAM", "Remote role limited to non-US geography"]
        if "apac" in location_text:
            return "skip", ["Remote role restricted to APAC", "Remote role limited to non-US geography"]
        if any(term in location_text for term in REMOTE_NON_US_TERMS):
            return "skip", ["Remote role limited to non-US geography"]
        if location_type == "hybrid" and state and state != "NV":
            return "stretch", ["Hybrid role outside Nevada", "Hybrid role outside target geography"]
        return "skip", ["Location is outside target geography"]
    if eligibility == "review" and "north america" in location_text:
        return "review", ["Remote North America requires manual review"]
    return "review", ["Location requires manual review"]


def extract_years_required(text: str) -> int | None:
    """Extract minimum years of experience required from job text."""
    lowered = text.lower()
    patterns = [
        r"\b(at least\s+)?(\d{1,2})\s*\+\s*years\b",
        r"\b(at least\s+)?(\d{1,2})\s+years\s+of\s+experience\b",
        r"\bat least\s+(\d{1,2})\s+years\b",
    ]
    vals=[]
    for pat in patterns:
        for m in re.finditer(pat, lowered):
            nums=[g for g in m.groups() if g and g.strip().isdigit()]
            if nums:
                vals.append(int(nums[-1]))
    return max(vals) if vals else None

ROLE_FAMILIES = {
    "product_management",
    "technical_product",
    "ai_builder",
    "product_engineering",
    "workflow_automation",
    "ai_operations",
    "developer_tools",
    "product_operations",
    "product_analytics",
    "marketing",
    "infrastructure_engineering",
    "sre",
    "security_engineering",
    "engineering",
    "data_science",
    "research",
    "customer_success",
    "executive",
    "unknown",
}


def classify_role_family(title: str) -> str:
    """Classify a role family based primarily on the title."""
    normalized = title.lower()

    if "chief of staff" in normalized:
        return "executive"
    if "data scientist" in normalized or "machine learning scientist" in normalized:
        return "data_science"
    if "researcher" in normalized or "research scientist" in normalized or "ux research" in normalized:
        return "research"
    if "product marketing" in normalized or "growth marketing" in normalized or "demand generation" in normalized:
        return "marketing"
    if "customer success" in normalized or "technical account manager" in normalized:
        return "customer_success"
    if "technical program manager" in normalized or "engineering program manager" in normalized:
        return "product_operations"
    if "technical product manager" in normalized:
        return "technical_product"
    if any(term in normalized for term in ("ai operations", "ml ops", "ai ops")):
        return "ai_operations"
    if any(term in normalized for term in ("workflow automation", "automation specialist", "automation engineer")):
        return "workflow_automation"
    if any(term in normalized for term in ("developer tools", "devtools", "platform product")):
        return "developer_tools"
    if "product engineer" in normalized:
        return "product_engineering"
    if any(term in normalized for term in ("ai builder", "agent builder", "agentic", "ai platform")):
        return "ai_builder"
    if "sre" in normalized or "site reliability" in normalized:
        return "sre"
    if "infrastructure" in normalized:
        return "infrastructure_engineering"
    if "security engineer" in normalized:
        return "security_engineering"
    if "engineer" in normalized or "developer" in normalized:
        return "engineering"
    if "product manager" in normalized or "technical product manager" in normalized:
        return "product_management"
    if "program manager" in normalized or "project manager" in normalized or "product operations" in normalized:
        return "product_operations"
    if any(term in normalized for term in ("chief", "vp ", "vice president", "head of", "director")):
        return "executive"
    return "unknown"


def _evaluate_location_fit(job: JobPosting, target_profile: TargetProfile) -> tuple[int, list[str], list[str], bool, bool]:
    """Evaluate location fit for Cody's remote/local constraints."""
    location_text = job.location.lower()
    workplace_type_text = job.workplace_type.lower()
    combined_location_text = f"{location_text} {workplace_type_text}"

    reasons: list[str] = []
    red_flags: list[str] = []

    remote_terms = [term.lower() for term in target_profile.acceptable_remote_terms]
    local_terms = [term.lower() for term in target_profile.local_terms]
    non_local_us_terms = [term.lower() for term in target_profile.non_remote_us_locations]
    excluded_terms = [term.lower() for term in target_profile.excluded_locations]

    has_unknown_location = not job.location.strip()
    has_unknown_workplace_type = not job.workplace_type.strip()
    has_unknown_geo = has_unknown_location and not any(
        term in combined_location_text for term in remote_terms + local_terms + non_local_us_terms + excluded_terms
    )
    has_remote = "remote" in combined_location_text
    has_remote_us = any(term in combined_location_text for term in remote_terms if "us" in term or "united states" in term)
    has_local = (not has_unknown_location) and any(term in location_text for term in local_terms)
    is_hybrid = "hybrid" in combined_location_text
    is_onsite = "onsite" in combined_location_text or "on-site" in combined_location_text

    score_delta = 0
    location_fit = False
    onsite_non_local_block = False

    if has_unknown_geo:
        score_delta += UNKNOWN_LOCATION_PENALTY

    if has_remote_us:
        score_delta += REMOTE_US_BONUS
        reasons.append(f"Location fit: Remote US ({REMOTE_US_BONUS:+d})")
        location_fit = True

    if has_local:
        score_delta += LOCAL_LOCATION_BONUS
        reasons.append(f"Location fit: Las Vegas/Henderson/Nevada ({LOCAL_LOCATION_BONUS:+d})")
        location_fit = True
        if is_hybrid:
            score_delta += HYBRID_LOCAL_BONUS
            reasons.append(f"Location fit: Hybrid in local market ({HYBRID_LOCAL_BONUS:+d})")

    has_international = any(term in location_text for term in excluded_terms)
    if has_international:
        penalty = ONSITE_INTERNATIONAL_PENALTY if is_onsite and not has_local and not has_remote_us else EXCLUDED_LOCATION_PENALTY
        score_delta += penalty
        red_flags.append("International location outside US/Las Vegas constraints")

    has_non_local_us = any(term in location_text for term in non_local_us_terms)
    has_explicit_non_local_hybrid = (
        is_hybrid
        and any(term in combined_location_text for term in NON_LOCAL_HYBRID_CITY_TERMS)
        and not any(term in combined_location_text for term in LOCAL_LOCATION_TERMS)
    )
    if (has_non_local_us and not has_remote_us) or has_explicit_non_local_hybrid:
        score_delta += US_NON_LOCAL_PENALTY
        if is_hybrid:
            red_flags.append("Hybrid in-office requirement outside Las Vegas/Nevada")
        else:
            red_flags.append("Onsite or location-specific US role outside Las Vegas/Nevada")

    if is_onsite and not has_local and not has_remote_us:
        onsite_non_local_block = True

    if has_remote and not has_remote_us and not has_local and has_unknown_location:
        red_flags.append("Remote role with unspecified geography")

    if is_hybrid and not has_remote_us and not has_local and has_unknown_location:
        score_delta += HYBRID_UNSPECIFIED_PENALTY
        red_flags.append("Hybrid role with unspecified location")

    if has_unknown_location and has_unknown_workplace_type:
        location_fit = False

    return score_delta, reasons, red_flags, location_fit, onsite_non_local_block


def explain_score(job: JobPosting, target_profile: TargetProfile) -> FitScore:
    """Return full scoring details for a job posting."""
    normalized_location = normalize_location(job.location, job.workplace_type)
    job.location_raw = normalized_location["location_raw"]
    job.normalized_country = normalized_location["normalized_country"]
    job.normalized_state = normalized_location["normalized_state"]
    job.normalized_city = normalized_location["normalized_city"]
    job.normalized_location_type = normalized_location["normalized_location_type"]
    job.geographic_eligibility = normalized_location["geographic_eligibility"]
    has_cursor_blank_location_limitation = (
        job.source.lower() == "ashby"
        and job.company.strip().lower() == "cursor"
        and not job.location_raw.strip()
    )
    if has_cursor_blank_location_limitation:
        job.geographic_eligibility = "review"
    text = f"{job.title} {job.description} {job.location} {job.workplace_type} {job.department} {job.team}".lower()
    title_text = job.title.lower()
    score = 0
    reasons: list[str] = []
    red_flags: list[str] = []

    title_hits = 0
    keyword_hits = 0

    for title in target_profile.target_titles:
        normalized = title.lower()
        if normalized in title_text:
            title_hits += 1
            score += BASE_TITLE_SCORE
            reasons.append(f"Title match: {title} (+{BASE_TITLE_SCORE})")

    for keyword in target_profile.target_keywords:
        normalized = keyword.lower()
        if normalized in text:
            keyword_hits += 1
            score += BASE_KEYWORD_SCORE
            reasons.append(f"Keyword match: {keyword} (+{BASE_KEYWORD_SCORE})")
    capability_boost = 0
    for keyword in AI_BUILDER_KEYWORDS:
        if keyword in text and capability_boost < AI_BUILDER_BONUS_CAP:
            applied = min(AI_BUILDER_KEYWORD_BONUS, AI_BUILDER_BONUS_CAP - capability_boost)
            capability_boost += applied
            score += applied
            reasons.append(f"AI builder capability match: {keyword} (+{applied})")

    company_name = job.company.strip().lower()
    priority_companies = {company.strip().lower() for company in target_profile.priority_companies}
    if company_name in priority_companies:
        score += PRIORITY_COMPANY_BONUS
        reasons.append(f"Priority company match (+{PRIORITY_COMPANY_BONUS})")

    local_priority_companies = {company.strip().lower() for company in target_profile.local_priority_companies}
    if company_name in local_priority_companies:
        score += LOCAL_PRIORITY_COMPANY_BONUS
        reasons.append(f"Local priority company match (+{LOCAL_PRIORITY_COMPANY_BONUS})")

    industry_text = f"{job.title} {job.description} {job.department} {job.team} {job.company}".lower()
    industry_boost = 0
    for term in target_profile.industry_bias:
        normalized = term.lower()
        if normalized in industry_text and industry_boost < INDUSTRY_BIAS_BONUS_CAP:
            applied = min(INDUSTRY_BIAS_BONUS, INDUSTRY_BIAS_BONUS_CAP - industry_boost)
            if applied <= 0:
                break
            industry_boost += applied
            score += applied
            reasons.append(f"Industry bias match: {term} (+{INDUSTRY_BIAS_BONUS})")

    for location in target_profile.preferred_locations:
        normalized = location.lower()
        if normalized in text:
            score += PREFERRED_LOCATION_SCORE
            reasons.append(f"Preferred location phrase: {location} (+{PREFERRED_LOCATION_SCORE})")

    location_score, location_reasons, location_flags, location_fit, onsite_non_local_block = _evaluate_location_fit(job, target_profile)
    score += location_score
    reasons.extend(location_reasons)
    red_flags.extend(location_flags)

    for keyword, points in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            red_flags.append(f"Mismatch keyword: {keyword} ({points})")
    hardcore_engineering_signal = any(keyword in text for keyword in NEGATIVE_ENGINEERING_KEYWORDS)
    if hardcore_engineering_signal:
        red_flags.append("Hardcore infrastructure/backend engineering emphasis detected")

    if not location_fit and title_hits > 0 and location_score <= EXCLUDED_LOCATION_PENALTY:
        red_flags.append("Role is title-aligned but location is not a fit")
        score = min(score, LOCATION_NOT_FIT_CAP)

    has_strong_match = title_hits > 0 and keyword_hits > 0
    lower_description = job.description.lower()
    is_product_marketing_manager = "product marketing manager" in text
    pmm_has_required_context = any(term in lower_description for term in PMM_HIGH_FIT_KEYWORDS)
    if is_product_marketing_manager and not pmm_has_required_context:
        has_strong_match = False
        red_flags.append("Product Marketing Manager role lacks product analytics/experimentation/AI/platform/customer-facing web context")

    classification = "low_fit"
    role_family = classify_role_family(job.title)
    if role_family not in ROLE_FAMILIES:
        role_family = "unknown"
    has_forced_low_fit_title = any(term in job.title.lower() for term in FORCED_LOW_FIT_TITLES)
    has_location_blocker = location_score <= EXCLUDED_LOCATION_PENALTY
    preferred_families = set(target_profile.preferred_role_families) if target_profile.preferred_role_families else set()
    disliked_families = set(target_profile.disliked_role_families) if target_profile.disliked_role_families else set()
    ai_native_families = {"ai_builder", "product_engineering", "workflow_automation", "ai_operations", "developer_tools", "technical_product"}
    is_high_fit_role_match = has_strong_match and role_family in {"product_management", "product_operations", "technical_product"}
    if role_family in ai_native_families and capability_boost >= 12:
        is_high_fit_role_match = True
    if is_high_fit_role_match and not has_location_blocker:
        classification = "high_fit"
    elif (
        any(term in text for term in NEAR_FIT_TERMS)
        or role_family in {"marketing", "customer_success", "research", "executive"}
        or (role_family in ai_native_families and capability_boost > 0)
        or (role_family in preferred_families and role_family not in disliked_families)
    ):
        classification = "near_fit"
    else:
        classification = "low_fit"

    if onsite_non_local_block and is_high_fit_role_match:
        classification = "near_fit"

    if role_family in {"engineering", "data_science", "infrastructure_engineering", "sre", "security_engineering"}:
        classification = "low_fit"
    if hardcore_engineering_signal and role_family not in {"product_engineering", "workflow_automation", "ai_builder", "ai_operations", "developer_tools"}:
        classification = "low_fit"
    if hardcore_engineering_signal and capability_boost < 12:
        classification = "low_fit"

    if has_forced_low_fit_title:
        classification = "low_fit"

    viability_score = 0
    viability_reasons: list[str] = []
    viability_level = "apply_now"
    location_viability_level, location_viability_reasons = evaluate_location_viability(job.location, job.workplace_type)
    viability_reasons.extend(location_viability_reasons)

    if location_viability_level == "skip":
        viability_score -= 90
    elif location_viability_level == "stretch":
        viability_score -= 45
    elif location_viability_level == "review":
        viability_score -= 15
    years_required = extract_years_required(text)
    if years_required is not None:
        if years_required >= 10:
            viability_score -= 50
            viability_reasons.append(f"Minimum experience requirement is {years_required}+ years")
        elif years_required >= 8:
            viability_score -= 30
            viability_reasons.append(f"Minimum experience requirement is {years_required}+ years")
        elif years_required >= 5:
            viability_score -= 10
            viability_reasons.append(f"Role asks for {years_required}+ years; review seniority fit")

    title_lower = job.title.lower()
    is_exec_level = any(term in title_lower for term in ("staff", "head of", "director", "vp ", "vice president"))
    if is_exec_level:
        viability_score -= 35
        viability_reasons.append("Senior leadership role level is likely a stretch")

    if role_family in {"ai_builder", "workflow_automation", "ai_operations"} and capability_boost > 0:
        viability_reasons.append("Strong AI workflow alignment")
    if "agentic" in text or "agents" in text:
        viability_reasons.append("Agentic systems overlap")
    if "developer tools" in text or role_family == "developer_tools":
        viability_reasons.append("Developer tooling overlap")

    if role_family == "engineering":
        viability_score -= 25
        viability_reasons.append("Engineering role should not be apply_now for Cody")

    has_senior_company_scope = ("10+ years" in text or (years_required or 0) >= 10) and (
        "senior capacity" in text or "company-level product decisions" in text
    )
    if has_senior_company_scope:
        viability_score -= 35
        viability_reasons.append("10+ years plus senior/company-level decision scope")

    if has_senior_company_scope or viability_score <= -70:
        viability_level = "skip"
    elif viability_score <= -35:
        viability_level = "stretch"
    elif viability_score <= -10:
        viability_level = "review"

    location_viability_rank = {"apply_now": 0, "review": 1, "stretch": 2, "skip": 3}
    if location_viability_rank[location_viability_level] > location_viability_rank[viability_level]:
        viability_level = location_viability_level
    if has_cursor_blank_location_limitation:
        viability_level = "review" if viability_level == "apply_now" else viability_level
        viability_reasons.append("Location unavailable from source; manual review required")

    return FitScore(
        total_score=max(0, score),
        classification=classification,
        role_family=role_family,
        viability_score=viability_score,
        viability_level=viability_level,
        viability_reasons=viability_reasons,
        reasons=reasons,
        red_flags=red_flags,
    )


def score_job(job: JobPosting, target_profile: TargetProfile) -> FitScore:
    """Score a job posting against role, domain, and location preferences."""
    return explain_score(job, target_profile)
