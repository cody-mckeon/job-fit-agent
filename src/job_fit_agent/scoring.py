"""Simple keyword-based scoring for job fit."""

from job_fit_agent.models import FitScore, JobPosting

ROLE_KEYWORDS = {
    "product manager": 30,
    "product owner": 28,
    "product operations": 24,
    "product ops": 24,
    "pm": 8,
}

DOMAIN_KEYWORDS = {
    "ai": 15,
    "analytics": 12,
    "data": 8,
    "web analytics": 12,
    "machine learning": 12,
}

LOCATION_KEYWORDS = {
    "remote": 18,
    "las vegas": 12,
    "hybrid": 10,
}

EXCLUSION_KEYWORDS = {
    "senior engineer": -35,
    "software engineer": -25,
    "sales": -30,
    "nurse": -40,
    "driver": -40,
}


def score_job(job: JobPosting) -> FitScore:
    """Score a job posting against basic role/domain/location preferences."""
    text = f"{job.title} {job.description} {job.location}".lower()
    score = 0
    reasons: list[str] = []
    red_flags: list[str] = []

    for keyword, points in ROLE_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Matched role keyword: {keyword}")

    for keyword, points in DOMAIN_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Matched domain keyword: {keyword}")

    for keyword, points in LOCATION_KEYWORDS.items():
        if keyword in text:
            score += points
            reasons.append(f"Matched location preference: {keyword}")

    for keyword, points in EXCLUSION_KEYWORDS.items():
        if keyword in text:
            score += points
            red_flags.append(f"Possible mismatch keyword: {keyword}")

    return FitScore(total_score=max(0, score), reasons=reasons, red_flags=red_flags)
