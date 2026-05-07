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

        location = ""
        location_obj = job.get("location")
        if isinstance(location_obj, dict):
            location = str(location_obj.get("locationName") or "").strip()
        if not location:
            location = str(job.get("locationName") or "").strip()

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
