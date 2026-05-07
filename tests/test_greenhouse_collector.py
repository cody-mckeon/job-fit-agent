from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import requests

from job_fit_agent.collectors.greenhouse import GreenhouseCollector


def test_fetch_jobs_parses_greenhouse_response(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "jobs": [
            {
                "title": "Software Engineer",
                "location": {"name": "San Francisco, CA"},
                "absolute_url": "https://jobs.example/1",
                "content": "Build great products",
                "updated_at": "2026-05-01T12:00:00Z",
            }
        ]
    }

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    collector = GreenhouseCollector(timeout=3)
    jobs = collector.fetch_jobs("openai")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "openai"
    assert job.title == "Software Engineer"
    assert job.location == "San Francisco, CA"
    assert job.url == "https://jobs.example/1"
    assert job.description == "Build great products"
    assert job.source == "greenhouse"
    assert job.date_found == datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def test_fetch_jobs_handles_failed_request(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", _raise)

    collector = GreenhouseCollector()
    jobs = collector.fetch_jobs("openai")

    assert jobs == []
