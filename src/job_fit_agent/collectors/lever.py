"""Lever job collector."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from job_fit_agent.models import JobPosting

LOGGER = logging.getLogger(__name__)
LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings/{company}?mode=json"


class LeverCollector:
    """Collects job postings from Lever public postings API."""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def validate_company_token(self, company: str) -> bool:
        """Return True when the Lever postings token is reachable."""
        url = LEVER_POSTINGS_URL.format(company=company)
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            status_code = (
                exc.response.status_code if getattr(exc, "response", None) is not None else None
            )
            if status_code == 404:
                LOGGER.warning("Lever company token not found for %s (404)", company)
            else:
                LOGGER.warning("Lever company token validation failed for %s: %s", company, exc)
            return False
        return True

    def fetch_jobs(self, company: str) -> list[JobPosting]:
        """Fetch jobs for a Lever company token and map to JobPosting models."""
        url = LEVER_POSTINGS_URL.format(company=company)
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            LOGGER.warning("Lever request failed for %s at %s: %s", company, url, exc)
            return []
        except ValueError:
            LOGGER.warning("Lever response JSON malformed for %s", company)
            return []

        if not isinstance(payload, list):
            return []

        jobs: list[JobPosting] = []
        for raw_job in payload:
            mapped = self._map_job(company=company, job=raw_job)
            if mapped is not None:
                jobs.append(mapped)
        return jobs

    def _map_job(self, company: str, job: Any) -> JobPosting | None:
        if not isinstance(job, dict):
            return None

        title = str(job.get("text") or "").strip()
        url = str(job.get("hostedUrl") or "").strip()
        if not title or not url:
            return None

        categories = job.get("categories")
        location = self._category_value(categories, "location")
        workplace_type = self._category_value(categories, "commitment")
        department = self._category_value(categories, "department")
        team = self._category_value(categories, "team")
        description = str(job.get("descriptionPlain") or job.get("description") or "").strip()

        return JobPosting(
            source="lever",
            company=company,
            title=title,
            location=location,
            workplace_type=workplace_type,
            department=department,
            team=team,
            url=url,
            description=description,
            date_found=datetime.now(timezone.utc),
        )

    @staticmethod
    def _category_value(categories: Any, key: str) -> str:
        if not isinstance(categories, dict):
            return ""
        return str(categories.get(key) or "").strip()
