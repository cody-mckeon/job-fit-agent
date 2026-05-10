"""Ashby job collector."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

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
        workplace_type = self._extract_workplace_type(job)

        if self._is_blank_or_vague_location(location):
            html_location = self._fetch_location_from_job_page(url)
            if html_location:
                location = html_location
                if not workplace_type:
                    inferred_workplace_type = self._infer_workplace_type_from_location(html_location)
                    if inferred_workplace_type:
                        workplace_type = inferred_workplace_type
        department = self._extract_field_name(job, ("departmentName", "department"))
        team = self._extract_field_name(job, ("teamName", "team"))

        description = str(job.get("descriptionPlain") or job.get("description") or "").strip()

        return JobPosting(
            source="ashby",
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

    def _extract_location(self, job: dict[str, Any]) -> str:
        """Build a best-effort location string from Ashby geographic metadata only."""
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

        address_obj = job.get("address")
        if isinstance(address_obj, dict):
            for key in ("city", "region", "state", "country", "countryCode"):
                _add_part(address_obj.get(key))
            _add_part(address_obj.get("formattedAddress"))
        elif isinstance(address_obj, str):
            _add_part(address_obj)

        return " | ".join(location_parts)


    def _is_blank_or_vague_location(self, location: str) -> bool:
        normalized = location.strip().lower()
        if not normalized:
            return True

        vague_terms = {
            "united states",
            "us",
            "usa",
            "global",
            "worldwide",
            "multiple locations",
            "various locations",
            "location flexible",
        }
        return normalized in vague_terms

    def _fetch_location_from_job_page(self, job_url: str) -> str:
        try:
            response = requests.get(job_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.debug("Unable to fetch Ashby job page %s: %s", job_url, exc)
            return ""

        soup = BeautifulSoup(response.text, "html.parser")
        location_header = soup.find("h2", string=lambda value: isinstance(value, str) and value.strip().lower() == "location")
        if location_header is None:
            return ""

        location_paragraph = location_header.find_next_sibling("p")
        if location_paragraph is None:
            return ""

        return " ".join(location_paragraph.get_text(" ", strip=True).split())

    def _infer_workplace_type_from_location(self, location: str) -> str:
        normalized = location.lower()
        if "remote" in normalized:
            return "Remote"
        if "hybrid" in normalized:
            return "Hybrid"
        if "onsite" in normalized or "on-site" in normalized or "in office" in normalized:
            return "Onsite"
        return ""

    def _extract_workplace_type(self, job: dict[str, Any]) -> str:
        workplace = str(job.get("workplaceType") or "").strip()
        if not workplace:
            return ""
        normalized = workplace.lower()
        if "remote" in normalized:
            return "Remote"
        if "hybrid" in normalized:
            return "Hybrid"
        if "on" in normalized and "site" in normalized:
            return "Onsite"
        return workplace

    def _extract_field_name(self, job: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = job.get(key)
            if isinstance(value, dict):
                name = str(value.get("name") or "").strip()
                if name:
                    return name
            else:
                text = str(value or "").strip()
                if text:
                    return text
        return ""
