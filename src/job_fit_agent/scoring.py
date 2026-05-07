"""Keyword-based scoring for job fit."""

from __future__ import annotations

from job_fit_agent.config import TargetProfile
from job_fit_agent.models import FitScore, JobPosting

BASE_TITLE_SCORE = 26
BASE_KEYWORD_SCORE = 8
PREFERRED_LOCATION_SCORE = 12
EXCLUDED_LOCATION_PENALTY = -35
EXTREME_STRENGTH_SCORE = 90

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
            reasons.append(f"Preferred location: {location} (+{PREFERRED_LOCATION_SCORE})")

    if "remote" in text and "remote us" not in text:
        score += 10
        reasons.append("Location match: remote (+10)")

    excluded_hit = False
    for location in target_profile.excluded_locations:
        normalized = location.lower()
        if normalized in text:
            excluded_hit = True
            score += EXCLUDED_LOCATION_PENALTY
            red_flags.append(f"Excluded location: {location} ({EXCLUDED_LOCATION_PENALTY})")

    for keyword, points in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            red_flags.append(f"Mismatch keyword: {keyword} ({points})")

    if excluded_hit and (title_hits < 2 or keyword_hits < 4):
        red_flags.append("Excluded location fails strong-match gate")
        score = min(score, 44)
    elif excluded_hit and score >= EXTREME_STRENGTH_SCORE:
        reasons.append("Excluded location overridden by extremely strong title/keyword alignment")

    return FitScore(total_score=max(0, score), reasons=reasons, red_flags=red_flags)


def score_job(job: JobPosting, target_profile: TargetProfile) -> FitScore:
    """Score a job posting against role, domain, and location preferences."""
    return explain_score(job, target_profile)
