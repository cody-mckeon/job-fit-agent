"""Configuration objects for job-fit-agent."""

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Runtime configuration for the application."""

    preferred_locations: list[str] = Field(default_factory=lambda: ["remote", "las vegas", "hybrid"])
