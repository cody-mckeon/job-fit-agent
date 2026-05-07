"""Keyword-based scoring for job fit."""

from job_fit_agent.models import FitScore, JobPosting

STRONG_POSITIVE_KEYWORDS = {
    "senior product manager": 30,
    "technical product manager": 30,
    "product manager": 26,
    "product operations": 22,
    "product analytics": 22,
    "growth product": 20,
    "ai product": 20,
    "data product": 20,
    "web analytics": 18,
    "marketing technology": 16,
    "experimentation": 16,
    "personalization": 16,
    "agentic ai": 20,
    "analytics platform": 18,
    "customer-facing web products": 16,
}

MEDIUM_POSITIVE_KEYWORDS = {
    "analytics": 10,
    "data": 8,
    "ai": 10,
    "machine learning": 10,
    "platform": 8,
    "revenue": 6,
    "hospitality": 6,
    "fintech": 6,
    "marketplace": 6,
    "lifecycle": 6,
    "conversion": 6,
    "funnel": 6,
    "dashboard": 6,
    "a/b testing": 8,
}

LOCATION_POSITIVE_KEYWORDS = {
    "remote us": 16,
    "united states": 10,
    "remote": 12,
    "hybrid": 8,
    "las vegas": 8,
    "nevada": 6,
}

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


def explain_score(job: JobPosting) -> FitScore:
    """Return full scoring details for a job posting."""
    text = f"{job.title} {job.description} {job.location}".lower()
    score = 0
    reasons: list[str] = []
    red_flags: list[str] = []

    for keyword, points in STRONG_POSITIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Strong match: {keyword} (+{points})")

    for keyword, points in MEDIUM_POSITIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Medium match: {keyword} (+{points})")

    for keyword, points in LOCATION_POSITIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Location match: {keyword} (+{points})")

    for keyword, points in NEGATIVE_KEYWORDS.items():
        if keyword in text:
            score += points
            red_flags.append(f"Mismatch keyword: {keyword} ({points})")

    return FitScore(total_score=max(0, score), reasons=reasons, red_flags=red_flags)


def score_job(job: JobPosting) -> FitScore:
    """Score a job posting against role, domain, and location preferences."""
    return explain_score(job)
