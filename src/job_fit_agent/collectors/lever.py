"""Lever job collector."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from job_fit_agent.models import JobPosting

LOGGER = logging.getLogger(__name__)
LEVER_BOARD_URL = "https://jobs.lever.co/{company}"


def _normalize_text(value: str) -> str:
    return " ".join(value.split())


def normalize_lever_job_url(url: str, company: str | None = None) -> str:
    """Return a canonical Lever job URL without query strings or fragments."""
    base = LEVER_BOARD_URL.format(company=company) if company else "https://jobs.lever.co/"
    absolute_url = urljoin(base, url.strip())
    parsed = urlsplit(absolute_url)
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class LeverCollector:
    """Collects job postings from a Lever-hosted public board."""

    def __init__(self, timeout: int = 10) -> None:
        self.timeout = timeout

    def validate_company_token(self, company: str) -> bool:
        """Return True when the Lever jobs board token is reachable."""
        url = LEVER_BOARD_URL.format(company=company)
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
        """Fetch a Lever board and map posting cards to JobPosting models."""
        url = LEVER_BOARD_URL.format(company=company)
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("Lever request failed for %s at %s: %s", company, url, exc)
            return []

        html = getattr(response, "text", "")
        if isinstance(html, str) and html.strip():
            return self._parse_board(company=company, html=html)

        try:
            payload = response.json()
        except ValueError:
            return []
        return self._parse_api_payload(company=company, payload=payload)

    def _parse_api_payload(self, company: str, payload: Any) -> list[JobPosting]:
        if not isinstance(payload, list):
            return []
        jobs: list[JobPosting] = []
        for raw_job in payload:
            if not isinstance(raw_job, dict):
                continue
            title = str(raw_job.get("text") or "").strip()
            raw_url = str(raw_job.get("hostedUrl") or "").strip()
            if not title or not raw_url:
                continue
            categories = raw_job.get("categories") if isinstance(raw_job.get("categories"), dict) else {}
            location = str(categories.get("location") or "").strip()
            description = str(raw_job.get("descriptionPlain") or raw_job.get("description") or "").strip()
            jobs.append(
                JobPosting(
                    source="lever",
                    company=company,
                    title=title,
                    location=location,
                    location_raw=location,
                    workplace_type=str(categories.get("commitment") or "").strip(),
                    department=str(categories.get("department") or "").strip(),
                    team=str(categories.get("team") or "").strip(),
                    url=normalize_lever_job_url(raw_url, company=company),
                    description=description,
                    date_found=datetime.now(timezone.utc),
                )
            )
        return jobs

    def _parse_board(self, company: str, html: str) -> list[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        postings = soup.select(".posting") or soup.select('[data-qa="posting"]')
        if not postings:
            postings = self._json_ld_posting_nodes(soup)

        jobs: list[JobPosting] = []
        for posting in postings:
            mapped = self._map_posting(company=company, posting=posting)
            if mapped is not None:
                jobs.append(mapped)
        return jobs

    def _map_posting(self, company: str, posting: Tag | dict[str, Any]) -> JobPosting | None:
        if isinstance(posting, dict):
            return self._map_json_ld_posting(company=company, posting=posting)

        title = self._extract_title(posting)
        raw_url = self._extract_url(posting)
        if not title or not raw_url:
            LOGGER.debug("Skipping incomplete Lever posting", extra={"company": company, "title": title, "url": raw_url})
            return None

        url = normalize_lever_job_url(raw_url, company=company)
        location = self._category_text(posting, "location")
        workplace_type = self._category_text(posting, "workplaceType") or self._category_text(posting, "commitment")
        department = self._extract_department(posting)
        team = self._category_text(posting, "team")
        description = self._extract_embedded_description(posting) or self._fetch_description(url)

        return JobPosting(
            source="lever",
            company=company,
            title=title,
            location=location,
            location_raw=location,
            workplace_type=workplace_type,
            department=department,
            team=team,
            url=url,
            description=description,
            date_found=datetime.now(timezone.utc),
        )

    def _map_json_ld_posting(self, company: str, posting: dict[str, Any]) -> JobPosting | None:
        title = str(posting.get("title") or "").strip()
        raw_url = str(posting.get("url") or "").strip()
        if not title or not raw_url:
            return None
        location = self._json_ld_location(posting.get("jobLocation"))
        department = str(posting.get("industry") or posting.get("occupationalCategory") or "").strip()
        description = BeautifulSoup(str(posting.get("description") or ""), "html.parser").get_text(" ", strip=True)
        return JobPosting(
            source="lever",
            company=company,
            title=title,
            location=location,
            location_raw=location,
            workplace_type=str(posting.get("employmentType") or "").strip(),
            department=department,
            url=normalize_lever_job_url(raw_url, company=company),
            description=_normalize_text(description),
            date_found=datetime.now(timezone.utc),
        )

    @staticmethod
    def _extract_title(posting: Tag) -> str:
        selectors = (
            '[data-qa="posting-name"]',
            ".posting-title h5",
            ".posting-title",
            "a",
            "h5",
            "h4",
        )
        for selector in selectors:
            node = posting.select_one(selector)
            if node is not None:
                text = _normalize_text(node.get_text(" ", strip=True))
                if text:
                    return text
        return ""

    @staticmethod
    def _extract_url(posting: Tag) -> str:
        link = posting if posting.name == "a" else posting.select_one("a[href]")
        if isinstance(link, Tag):
            return str(link.get("href") or "").strip()
        return ""

    @staticmethod
    def _category_text(posting: Tag, category: str) -> str:
        class_selector = category.lower()
        selectors = (
            f'.sort-by-{class_selector}',
            f'.posting-category.{class_selector}',
            f'[data-qa="posting-{class_selector}"]',
        )
        if category == "workplaceType":
            selectors = (
                ".sort-by-workplace-type",
                ".posting-category.workplace-type",
                '[data-qa="posting-workplace-type"]',
            )
        for selector in selectors:
            node = posting.select_one(selector)
            if node is not None:
                text = _normalize_text(node.get_text(" ", strip=True))
                if text:
                    return text
        return ""

    @staticmethod
    def _extract_department(posting: Tag) -> str:
        department = LeverCollector._category_text(posting, "department")
        if department:
            return department
        group = posting.find_parent(class_="postings-group")
        if isinstance(group, Tag):
            label = group.select_one(".posting-category-title, .large-category-label, h3")
            if label is not None:
                return _normalize_text(label.get_text(" ", strip=True))
        return ""

    @staticmethod
    def _extract_embedded_description(posting: Tag) -> str:
        node = posting.select_one(".posting-description, .description, [data-qa='posting-description']")
        if node is None:
            return ""
        return _normalize_text(node.get_text(" ", strip=True))

    def _fetch_description(self, url: str) -> str:
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException:
            return ""
        return self._parse_detail_description(response.text)

    @staticmethod
    def _parse_detail_description(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for unwanted in soup.select("script, style, nav, footer, header, .posting-headline"):
            unwanted.decompose()
        node = soup.select_one(".posting-page .section-wrapper")
        if node is None:
            node = soup.select_one(".content-wrapper")
        if node is None:
            node = soup.select_one("main")
        if node is None:
            node = soup.body
        if node is None:
            return ""
        return _normalize_text(node.get_text(" ", strip=True))

    @staticmethod
    def _json_ld_posting_nodes(soup: BeautifulSoup) -> list[dict[str, Any]]:
        postings: list[dict[str, Any]] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw = script.string or script.get_text(strip=True)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            nodes = payload if isinstance(payload, list) else [payload]
            for node in nodes:
                if isinstance(node, dict) and str(node.get("@type", "")).lower() == "jobposting":
                    postings.append(node)
        return postings

    @staticmethod
    def _json_ld_location(value: Any) -> str:
        nodes = value if isinstance(value, list) else [value]
        locations: list[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            address = node.get("address")
            if isinstance(address, dict):
                parts = [
                    str(address.get(key) or "").strip()
                    for key in ("addressLocality", "addressRegion", "addressCountry")
                    if str(address.get(key) or "").strip()
                ]
                if parts:
                    locations.append(", ".join(parts))
            name = str(node.get("name") or "").strip()
            if name:
                locations.append(name)
        return "; ".join(dict.fromkeys(locations))
