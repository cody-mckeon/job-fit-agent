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
    "product engineer",
    "member of technical staff",
    "infrastructure engineer",
    "engineering manager",
}

NON_LOCAL_HYBRID_CITY_TERMS = {
    "foster city, ca",
    "san francisco, ca",
    "new york, ny",
    "sf",
    "ny",
}

LOCAL_LOCATION_TERMS = ("las vegas", "henderson", "nevada", " nv", "remote us", "us remote", "remote united states")


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
    "product_operations",
    "marketing",
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
        red_flags.append("Location not specified")

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
    is_high_fit_role_match = has_strong_match and role_family in {"product_management", "product_operations"}
    if is_high_fit_role_match and not has_location_blocker:
        classification = "high_fit"
    elif any(term in text for term in NEAR_FIT_TERMS) or role_family in {"marketing", "customer_success", "research", "executive"}:
        classification = "near_fit"
    else:
        classification = "low_fit"

    if onsite_non_local_block and is_high_fit_role_match:
        classification = "near_fit"

    if role_family in {"engineering", "data_science"}:
        classification = "low_fit"

    if has_forced_low_fit_title:
        classification = "low_fit"

    viability_score = 0
    viability_reasons: list[str] = []
    viability_level = "apply_now"
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

    if "product engineer" in title_lower:
        viability_score -= 25
        viability_reasons.append("Product Engineer role is a lower fit for target profile")

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
