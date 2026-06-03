"""Ashby job collector."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from job_fit_agent.models import JobPosting

LOGGER = logging.getLogger(__name__)
ASHBY_BOARD_URL = "https://api.ashbyhq.com/posting-api/job-board/{company}"

def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def extract_ashby_hydration_data(html: str) -> dict[str, str]:
    """Extract metadata from Next.js hydration payload when present."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None:
        return {}

    raw_payload = script.string or script.get_text(strip=True)
    if not raw_payload:
        return {}

    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        LOGGER.debug("Ashby hydration payload present but invalid JSON")
        return {}

    metadata: dict[str, str] = {}

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            key = str(node.get("key") or "").strip().lower()
            value = str(node.get("value") or "").strip()
            if key in {"location", "workplace type", "location type", "employment type", "department", "team"} and value:
                mapping = {
                    "location": "Location",
                    "workplace type": "Location Type",
                    "location type": "Location Type",
                    "employment type": "Employment Type",
                    "department": "Department",
                    "team": "Team",
                }
                metadata[mapping[key]] = value

            for dict_key, dict_val in node.items():
                lowered = dict_key.lower()
                text = str(dict_val or "").strip() if not isinstance(dict_val, (dict, list)) else ""
                if lowered in {"location", "locationname"} and text:
                    metadata.setdefault("Location", text)
                elif lowered in {"workplacetype", "locationtype"} and text:
                    metadata.setdefault("Location Type", text)
                elif lowered in {"employmenttype", "employmenttypename"} and text:
                    metadata.setdefault("Employment Type", text)
                elif lowered == "department" and text:
                    metadata.setdefault("Department", text)
                elif lowered == "team" and text:
                    metadata.setdefault("Team", text)
                _walk(dict_val)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return metadata


def dump_sidebar_metadata(html: str) -> dict[str, str]:
    """Debug helper to inspect parsed Ashby sidebar metadata."""
    metadata = parse_ashby_sidebar_metadata(html)
    LOGGER.debug("Ashby sidebar metadata dump: %s", metadata)
    return metadata


def extract_ashby_app_data_metadata(html: str) -> dict[str, str]:
    """Extract metadata from window.__appData assignment."""
    match = re.search(r"window\.__appData\s*=\s*(\{.*?\})\s*;", html, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        LOGGER.debug("Ashby appData payload present but invalid JSON")
        return {}

    posting = payload.get("posting") if isinstance(payload, dict) else None
    if not isinstance(posting, dict):
        return {}

    metadata: dict[str, str] = {}
    location = str(posting.get("locationExternalName") or posting.get("locationName") or "").strip()
    if location:
        metadata["Location"] = location

    workplace = str(posting.get("workplaceType") or "").strip()
    if workplace:
        metadata["Location Type"] = workplace

    department = str(posting.get("departmentExternalName") or posting.get("departmentName") or "").strip()
    if department:
        metadata["Department"] = department

    team = str(posting.get("teamExternalName") or posting.get("teamName") or "").strip()
    if team:
        metadata["Team"] = team

    employment_type = str(posting.get("employmentType") or posting.get("employmentTypeName") or "").strip()
    if employment_type:
        metadata["Employment Type"] = employment_type

    is_remote = posting.get("isRemote")
    if isinstance(is_remote, bool):
        metadata["is_remote"] = str(is_remote)

    postal = posting.get("address", {}).get("postalAddress", {}) if isinstance(posting.get("address"), dict) else {}
    if isinstance(postal, dict):
        for source_key, target_key in (
            ("addressLocality", "city"),
            ("addressRegion", "state"),
            ("addressCountry", "country"),
        ):
            value = str(postal.get(source_key) or "").strip()
            if value:
                metadata[target_key] = value

    applicant_country = ""
    app_req = posting.get("applicantLocationRequirements")
    if isinstance(app_req, dict):
        applicant_country = str(app_req.get("name") or "").strip()
    if applicant_country:
        metadata["applicant_country"] = applicant_country
    return metadata


def extract_ashby_json_ld_metadata(html: str) -> dict[str, str]:
    """Extract metadata from JobPosting JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", attrs={"type": "application/ld+json"})
    for script in scripts:
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = payload if isinstance(payload, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict) or str(node.get("@type", "")).lower() != "jobposting":
                continue
            metadata: dict[str, str] = {}
            job_location = node.get("jobLocation")
            if isinstance(job_location, dict):
                address = job_location.get("address", {})
                if isinstance(address, dict):
                    for source_key, target_key in (
                        ("addressLocality", "city"),
                        ("addressRegion", "state"),
                        ("addressCountry", "country"),
                    ):
                        value = str(address.get(source_key) or "").strip()
                        if value:
                            metadata[target_key] = value
            location_type = str(node.get("jobLocationType") or "").strip()
            if location_type:
                metadata["Location Type"] = location_type
            app_req = node.get("applicantLocationRequirements")
            if isinstance(app_req, dict):
                app_name = str(app_req.get("name") or "").strip()
                if app_name:
                    metadata["applicant_country"] = app_name
            return metadata
    return {}


def parse_ashby_sidebar_metadata(html: str) -> dict[str, str]:
    """Parse visible sidebar metadata using semantic label/value traversal."""
    soup = BeautifulSoup(html, "html.parser")
    labels = ("Location", "Location Type", "Employment Type", "Department", "Compensation")
    label_set = {label.lower(): label for label in labels}
    metadata: dict[str, str] = {}

    def _first_text_from_container(node: Any, label_key: str) -> str:
        for candidate in node.find_all(["p", "span", "div", "li"], recursive=True):
            candidate_text = _normalize_text(candidate.get_text(" ", strip=True))
            if not candidate_text:
                continue
            if candidate_text.lower() == label_key:
                continue
            return candidate_text
        return ""

    elements = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "span", "div", "dt"])
    for element in elements:
        label_text = _normalize_text(element.get_text(" ", strip=True))
        if not label_text:
            continue
        canonical_label = label_set.get(label_text.lower())
        if not canonical_label or canonical_label in metadata:
            continue

        value = ""
        sibling = element.find_next_sibling()
        while sibling and not value:
            value = _normalize_text(sibling.get_text(" ", strip=True))
            sibling = sibling.find_next_sibling() if not value else sibling

        if not value and element.parent is not None:
            parent_text = _first_text_from_container(element.parent, canonical_label.lower())
            value = parent_text

        if not value:
            next_el = element.find_next(lambda tag: tag is not element and hasattr(tag, "get_text"))
            if next_el is not None:
                candidate = _normalize_text(next_el.get_text(" ", strip=True))
                if candidate.lower() != canonical_label.lower():
                    value = candidate

        if value:
            metadata[canonical_label] = value

    if not metadata:
        LOGGER.debug("Ashby sidebar metadata extraction failed: no semantic labels found")
    elif "Location" not in metadata:
        LOGGER.debug("Ashby sidebar metadata extraction incomplete: missing Location label")

    return metadata


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

        page_metadata = self._fetch_sidebar_metadata_from_job_page(url)

        used_sidebar_location = False
        if self._is_blank_or_vague_location(location):
            sidebar_location = page_metadata.get("Location", "")
            if sidebar_location:
                location = sidebar_location
                used_sidebar_location = True
            else:
                built = self._build_location_from_metadata(page_metadata)
                if built:
                    location = built
                    used_sidebar_location = True

        if not workplace_type:
            sidebar_workplace_type = page_metadata.get("Location Type", "")
            if sidebar_workplace_type:
                workplace_type = self._normalize_workplace_type(sidebar_workplace_type)
            elif used_sidebar_location and location:
                inferred_workplace_type = self._infer_workplace_type_from_location(location)
                if inferred_workplace_type:
                    workplace_type = inferred_workplace_type

        department = self._extract_field_name(job, ("departmentName", "department"))
        if not department:
            department = page_metadata.get("Department", "")

        employment_type = page_metadata.get("Employment Type", "")

        team = self._extract_field_name(job, ("teamName", "team"))
        if not team:
            team = page_metadata.get("Team", "")

        description = str(job.get("descriptionPlain") or job.get("description") or "").strip()

        return JobPosting(
            source="ashby",
            company=company,
            title=title,
            location=location,
            workplace_type=workplace_type,
            department=department,
            employment_type=employment_type,
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

    def _fetch_sidebar_metadata_from_job_page(self, job_url: str) -> dict[str, str]:
        try:
            response = requests.get(job_url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.debug("Unable to fetch Ashby job page %s: %s", job_url, exc)
            return {}

        html = response.text
        app_metadata = extract_ashby_app_data_metadata(html)
        json_ld_metadata = extract_ashby_json_ld_metadata(html)
        hydration_metadata = extract_ashby_hydration_data(html)
        sidebar_metadata = parse_ashby_sidebar_metadata(html)

        metadata: dict[str, str] = {}
        for source in (sidebar_metadata, json_ld_metadata, hydration_metadata, app_metadata):
            metadata.update({k: v for k, v in source.items() if v})

        if not metadata:
            LOGGER.debug("Ashby metadata extraction failed for %s", job_url)
        return metadata

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
        return self._normalize_workplace_type(workplace)

    def _normalize_workplace_type(self, workplace: str) -> str:
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

    def _build_location_from_metadata(self, metadata: dict[str, str]) -> str:
        city = metadata.get("city", "").strip()
        state = metadata.get("state", "").strip()
        country = metadata.get("country", "").strip()
        applicant_country = metadata.get("applicant_country", "").strip()
        workplace = self._normalize_workplace_type(metadata.get("Location Type", ""))

        if city and state:
            return f"{city}, {state}"
        if workplace == "Remote":
            basis = applicant_country or country
            if basis:
                return f"Remote {basis}"
        if city or state or country:
            return ", ".join(part for part in (city, state, country) if part)
        return ""
