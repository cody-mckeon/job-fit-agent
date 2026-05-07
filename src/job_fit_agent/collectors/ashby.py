"""Ashby job collector."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from job_fit_agent.models import JobPosting

LOGGER = logging.getLogger(__name__)
ASHBY_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{company}"


class AshbyCollector:
    """Collects job postings from Ashby public posting API."""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def fetch_jobs(self, company: str) -> list[JobPosting]:
        """Fetch jobs for an Ashby board and map them to `JobPosting` models."""
        url = ASHBY_BOARD_URL.format(company=company)
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            LOGGER.warning("Ashby request failed for %s at %s: %s", company, url, exc)
            return []
        except ValueError:
            LOGGER.warning("Ashby response JSON malformed for %s", company)
            return []

        jobs_raw = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs_raw, list):
            return []

        mapped_jobs: list[JobPosting] = []
        for job in jobs_raw:
            mapped = self._map_job(company=company, job=job)
            if mapped is not None:
                mapped_jobs.append(mapped)
        return mapped_jobs

    def _map_job(self, company: str, job: Any) -> JobPosting | None:
        if not isinstance(job, dict):
            return None

        title = str(job.get("title") or "").strip()
        url = str(job.get("jobUrl") or "").strip()
        if not title or not url:
            return None

        location = self._extract_location(job)

        description = str(job.get("descriptionPlain") or job.get("description") or "").strip()

        return JobPosting(
            source="ashby",
            company=company,
            title=title,
            location=location,
            url=url,
            description=description,
            date_found=datetime.now(timezone.utc),
        )

    def _extract_location(self, job: dict[str, Any]) -> str:
        """Build a best-effort location string from Ashby location metadata."""
        location_parts: list[str] = []

        def _add_part(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in location_parts:
                location_parts.append(text)

        location_obj = job.get("location")
        if isinstance(location_obj, dict):
            _add_part(location_obj.get("locationName"))
            _add_part(location_obj.get("name"))

        _add_part(job.get("locationName"))

        workplace_type = str(job.get("workplaceType") or "").strip().lower()
        if workplace_type:
            if "remote" in workplace_type:
                if any("us" in part.lower() or "united states" in part.lower() for part in location_parts):
                    return "Remote US"
                return "Remote"
            _add_part(job.get("workplaceType"))

        address_obj = job.get("address")
        if isinstance(address_obj, dict):
            for key in ("city", "region", "state", "country", "countryCode"):
                _add_part(address_obj.get(key))
            _add_part(address_obj.get("formattedAddress"))
        elif isinstance(address_obj, str):
            _add_part(address_obj)

        for key in ("departmentName", "teamName", "department", "team"):
            value = job.get(key)
            if isinstance(value, dict):
                _add_part(value.get("name"))
            else:
                _add_part(value)

        return " | ".join(location_parts)
