"""Stable, source-native job identity helpers.

Stable job keys must never depend on local SQLite row ids.  They are used by
Telegram commands, durable application status records, package metadata, and CI
runs where SQLite row ids may differ.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

STABLE_JOB_KEY_RE = re.compile(r"^(greenhouse|ashby|lever|job):([^:]+):(.+)$")


def _value(job: object, key: str, default: Any = "") -> Any:
    if isinstance(job, dict):
        return job.get(key, default)
    try:
        return job[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return getattr(job, key, default)


def normalize_company_slug(company: str) -> str:
    """Normalize a company name/board token for stable key use."""
    value = str(company or "").strip().lower()
    value = re.sub(r"['’]", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "company"


def _payload_values(payload: Any, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    if not payload:
        return values
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return values
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if value not in (None, ""):
                values.append(str(value))
        for nested_key in ("job", "posting", "data"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                values.extend(_payload_values(nested, names))
    return values


def _last_path_segment(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    return segments[-1] if segments else ""


def _company_from_url(source: str, url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    segments = [segment for segment in parsed.path.split("/") if segment]
    if source == "ashby" and host == "jobs.ashbyhq.com" and segments:
        return segments[0]
    if source == "lever" and host == "jobs.lever.co" and segments:
        return segments[0]
    if source == "greenhouse" and host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} and segments:
        return segments[0]
    # Company-hosted Greenhouse pages such as https://stripe.com/jobs/search?gh_jid=...
    if source == "greenhouse" and host:
        parts = host.split(".")
        if len(parts) >= 2:
            return parts[-2]
    return ""


def infer_source_from_url(url: str) -> str | None:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.lower()
    if host == "jobs.ashbyhq.com":
        return "ashby"
    if host == "jobs.lever.co":
        return "lever"
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} or "gh_jid" in parse_qs(parsed.query):
        return "greenhouse"
    return None


def extract_external_job_id(source: str, url: str = "", payload: Any = None) -> str:
    """Extract the source-native external job id for a job.

    Greenhouse prefers gh_jid from URL/payload and canonical board payload ids;
    Ashby and Lever use their source URL path identifiers.  Local SQLite ids are
    intentionally ignored.
    """
    normalized_source = str(source or infer_source_from_url(url) or "job").strip().lower()
    parsed = urlparse(str(url or ""))
    query = parse_qs(parsed.query)

    if normalized_source == "greenhouse":
        for name in ("gh_jid", "gh_src"):
            value = query.get(name, [""])[0]
            if name == "gh_jid" and value:
                return str(value).strip()
        payload_values = _payload_values(payload, ("gh_jid", "ghJid", "greenhouse_job_id", "job_id", "id"))
        for value in payload_values:
            if value.strip():
                return value.strip()
        if parsed.netloc.lower() in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
            segment = _last_path_segment(url)
            if segment:
                return segment
        return ""

    if normalized_source == "ashby":
        payload_values = _payload_values(payload, ("ashby_uuid", "jobId", "job_id", "id"))
        if parsed.netloc.lower() == "jobs.ashbyhq.com":
            segment = _last_path_segment(url)
            if segment:
                return segment
        return next((value.strip() for value in payload_values if value.strip()), "")

    if normalized_source == "lever":
        payload_values = _payload_values(payload, ("lever_posting_id", "posting_id", "id", "slug"))
        if parsed.netloc.lower() == "jobs.lever.co":
            segment = _last_path_segment(url)
            if segment:
                return segment
        return next((value.strip() for value in payload_values if value.strip()), "")

    return ""


def deterministic_unknown_job_hash(job: object) -> str:
    source = str(_value(job, "source", "") or "")
    url = str(_value(job, "url", "") or "")
    title = str(_value(job, "title", "") or "")
    company = str(_value(job, "company", "") or "")
    basis = "\n".join([source.lower().strip(), url.strip(), title.lower().strip(), company.lower().strip()])
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def build_stable_job_key(job: object) -> str:
    """Build a deterministic, source-native stable job key for a job-like object."""
    url = str(_value(job, "url", "") or "")
    payload = _value(job, "payload", None)
    source = str(_value(job, "source", "") or infer_source_from_url(url) or "job").strip().lower()
    if source not in {"greenhouse", "ashby", "lever"}:
        source = infer_source_from_url(url) or "job"
    company = str(_value(job, "company", "") or _company_from_url(source, url) or "company")
    company_slug = normalize_company_slug(company)
    external_id = extract_external_job_id(source, url, payload)
    if source in {"greenhouse", "ashby", "lever"} and external_id:
        return f"{source}:{company_slug}:{external_id}"
    return f"job:{company_slug}:{deterministic_unknown_job_hash(job)}"


def parse_stable_job_key_value(stable_job_key: str) -> tuple[str, str, str]:
    match = STABLE_JOB_KEY_RE.match(str(stable_job_key or "").strip())
    if not match:
        raise ValueError("Stable job key must use source:company:external_job_id.")
    return match.group(1), match.group(2), match.group(3)


def validate_stable_job_key(job: object, stable_job_key: str | None = None) -> list[str]:
    """Return validation warnings for a job/key pair."""
    key = stable_job_key or build_stable_job_key(job)
    warnings: list[str] = []
    try:
        source, company, external_id = parse_stable_job_key_value(key)
    except ValueError as exc:
        return [str(exc)]
    canonical = build_stable_job_key(job)
    if key != canonical:
        warnings.append(f"Stable key mismatch: expected {canonical}.")
    job_source = str(_value(job, "source", "") or infer_source_from_url(str(_value(job, "url", "") or "")) or "").lower()
    if job_source and source != job_source and source != "job":
        warnings.append(f"Identifier source {source} does not match job source {job_source}.")
    job_company = normalize_company_slug(str(_value(job, "company", "") or _company_from_url(source, str(_value(job, "url", "") or ""))))
    if company != job_company:
        warnings.append(f"Identifier company {company} does not match job company {job_company}.")
    source_external_id = extract_external_job_id(source, str(_value(job, "url", "") or ""), _value(job, "payload", None))
    if source in {"greenhouse", "ashby", "lever"} and source_external_id and external_id != source_external_id:
        warnings.append(f"Identifier external id {external_id} does not match source external id {source_external_id}.")
    return warnings
