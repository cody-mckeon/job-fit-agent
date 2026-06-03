"""Core domain models for job-fit-agent."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class JobPosting(BaseModel):
    source: str
    company: str
    title: str
    location: str
    url: str
    description: str
    workplace_type: str = ""
    location_raw: str = ""
    normalized_country: str = ""
    normalized_state: str = ""
    normalized_city: str = ""
    normalized_location_type: str = ""
    geographic_eligibility: str = "review"
    geographic_reason: str = ""
    department: str = ""
    employment_type: str = ""
    team: str = ""
    date_found: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FitScore(BaseModel):
    total_score: int
    classification: str
    role_family: str
    viability_score: int = 0
    viability_level: str = "review"
    viability_reasons: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
