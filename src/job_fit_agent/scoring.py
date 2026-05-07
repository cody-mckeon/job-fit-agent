"""Keyword-based scoring for job fit."""

from __future__ import annotations

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
US_NON_LOCAL_PENALTY = -28

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


def _evaluate_location_fit(job: JobPosting, target_profile: TargetProfile) -> tuple[int, list[str], list[str], bool]:
    """Evaluate location fit for Cody's remote/local constraints."""
    location_text = job.location.lower()
    description_text = job.description.lower()
    combined_text = f"{location_text} {description_text}"

    reasons: list[str] = []
    red_flags: list[str] = []

    remote_terms = [term.lower() for term in target_profile.acceptable_remote_terms]
    local_terms = [term.lower() for term in target_profile.local_terms]
    non_local_us_terms = [term.lower() for term in target_profile.non_remote_us_locations]
    excluded_terms = [term.lower() for term in target_profile.excluded_locations]

    has_remote = "remote" in combined_text
    has_remote_us = any(term in combined_text for term in remote_terms if "us" in term or "united states" in term)
    has_local = any(term in combined_text for term in local_terms)
    is_hybrid = "hybrid" in combined_text

    score_delta = 0
    location_fit = False

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

    if any(term in combined_text for term in excluded_terms):
        score_delta += EXCLUDED_LOCATION_PENALTY
        red_flags.append("International location outside US/Las Vegas constraints")

    has_non_local_us = any(term in combined_text for term in non_local_us_terms)
    if has_non_local_us and not has_remote_us:
        score_delta += US_NON_LOCAL_PENALTY
        red_flags.append("Onsite or location-specific US role outside Las Vegas/Nevada")

    if has_remote and not has_remote_us and not has_local:
        red_flags.append("Remote language found but not clearly Remote US")

    return score_delta, reasons, red_flags, location_fit


def explain_score(job: JobPosting, target_profile: TargetProfile) -> FitScore:
    """Return full scoring details for a job posting."""
    text = f"{job.title} {job.description} {job.location}".lower()
    score = 0
    reasons: list[str] = []
    red_flags: list[str] = []

    title_hits = 0
    keyword_hits = 0

    for title in target_profile.target_titles:
        normalized = title.lower()
        if normalized in text:
            title_hits += 1
            score += BASE_TITLE_SCORE
            reasons.append(f"Title match: {title} (+{BASE_TITLE_SCORE})")

    for keyword in target_profile.target_keywords:
        normalized = keyword.lower()
        if normalized in text:
            keyword_hits += 1
            score += BASE_KEYWORD_SCORE
            reasons.append(f"Keyword match: {keyword} (+{BASE_KEYWORD_SCORE})")

    for location in target_profile.preferred_locations:
        normalized = location.lower()
        if normalized in text:
            score += PREFERRED_LOCATION_SCORE
            reasons.append(f"Preferred location phrase: {location} (+{PREFERRED_LOCATION_SCORE})")

    location_score, location_reasons, location_flags, location_fit = _evaluate_location_fit(job, target_profile)
    score += location_score
    reasons.extend(location_reasons)
    red_flags.extend(location_flags)

    for keyword, points in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            red_flags.append(f"Mismatch keyword: {keyword} ({points})")

    if not location_fit and title_hits > 0:
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
    if location_fit and has_strong_match:
        classification = "high_fit"
    elif any(term in text for term in NEAR_FIT_TERMS):
        classification = "near_fit"

    return FitScore(total_score=max(0, score), classification=classification, reasons=reasons, red_flags=red_flags)


def score_job(job: JobPosting, target_profile: TargetProfile) -> FitScore:
    """Score a job posting against role, domain, and location preferences."""
    return explain_score(job, target_profile)
