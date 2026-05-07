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
    department: str = ""
    team: str = ""
    date_found: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FitScore(BaseModel):
    total_score: int
    classification: str
    reasons: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
