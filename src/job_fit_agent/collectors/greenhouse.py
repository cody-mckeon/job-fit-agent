"""Greenhouse job collector."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from job_fit_agent.models import JobPosting

LOGGER = logging.getLogger(__name__)
GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{company}/jobs"


class GreenhouseCollector:
    """Collects job postings from Greenhouse boards API."""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def fetch_jobs(self, company: str) -> list[JobPosting]:
        """Fetch jobs for a company board and map them to `JobPosting` models."""
        url = GREENHOUSE_BOARD_URL.format(company=company)
        LOGGER.info("Fetching Greenhouse jobs", extra={"company": company, "url": url})

        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except requests.Timeout:
            LOGGER.warning("Greenhouse request timed out", extra={"company": company})
            return []
        except requests.RequestException as exc:
            LOGGER.warning(
                "Greenhouse request failed",
                extra={"company": company, "error": str(exc)},
            )
            return []
        except ValueError as exc:
            LOGGER.warning(
                "Greenhouse response JSON malformed",
                extra={"company": company, "error": str(exc)},
            )
            return []

        jobs_raw = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(jobs_raw, list):
            LOGGER.warning(
                "Greenhouse response missing jobs list", extra={"company": company}
            )
            return []

        jobs: list[JobPosting] = []
        for job in jobs_raw:
            mapped = self._map_job(company=company, job=job)
            if mapped is not None:
                jobs.append(mapped)

        LOGGER.info(
            "Fetched Greenhouse jobs successfully",
            extra={"company": company, "job_count": len(jobs)},
        )
        return jobs

    def _map_job(self, company: str, job: Any) -> JobPosting | None:
        if not isinstance(job, dict):
            LOGGER.debug("Skipping malformed job record", extra={"company": company})
            return None

        title = str(job.get("title") or "").strip()
        url = str(job.get("absolute_url") or "").strip()
        if not title or not url:
            LOGGER.debug(
                "Skipping incomplete job record",
                extra={"company": company, "title": title, "url": url},
            )
            return None

        location_data = job.get("location")
        location = ""
        if isinstance(location_data, dict):
            location = str(location_data.get("name") or "").strip()

        description = str(job.get("content") or "").strip()

        updated_at_raw = job.get("updated_at")
        date_found = self._parse_updated_at(updated_at_raw)

        return JobPosting(
            source="greenhouse",
            company=company,
            title=title,
            location=location,
            url=url,
            description=description,
            date_found=date_found,
        )

    @staticmethod
    def _parse_updated_at(value: Any) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            try:
                normalized = value.replace("Z", "+00:00")
                parsed = datetime.fromisoformat(normalized)
                return (
                    parsed
                    if parsed.tzinfo is not None
                    else parsed.replace(tzinfo=timezone.utc)
                )
            except ValueError:
                LOGGER.debug("Invalid updated_at format", extra={"value": value})
        return datetime.now(timezone.utc)
