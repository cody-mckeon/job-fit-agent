from unittest.mock import Mock

import requests

from job_fit_agent.collectors.ashby import AshbyCollector


def test_fetch_jobs_parses_ashby_response(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "jobs": [
            {
                "title": "Software Engineer",
                "jobUrl": "https://jobs.example/1",
                "location": {"locationName": "Remote US"},
                "descriptionPlain": "Build AI products",
            }
        ]
    }
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "ashby"
    assert job.company == "anthropic"
    assert job.title == "Software Engineer"


def test_fetch_jobs_handles_failed_request(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", _raise)
    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs == []
