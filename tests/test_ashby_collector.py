from unittest.mock import Mock

import requests

from job_fit_agent.collectors.ashby import AshbyCollector
from job_fit_agent.config import load_target_profile
from job_fit_agent.scoring import score_job


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


def test_fetch_jobs_parses_location_name_field(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "jobs": [
            {
                "title": "PM",
                "jobUrl": "https://jobs.example/2",
                "locationName": "San Francisco, CA",
                "descriptionPlain": "desc",
            }
        ]
    }
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "San Francisco, CA"


def test_fetch_jobs_maps_remote_from_workplace_type(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "jobs": [
            {
                "title": "PM",
                "jobUrl": "https://jobs.example/3",
                "workplaceType": "remote",
                "locationName": "United States",
                "descriptionPlain": "desc",
            }
        ]
    }
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "Remote US"


def test_missing_location_maps_to_empty_and_scores_unknown(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "jobs": [
            {
                "title": "Product Manager",
                "jobUrl": "https://jobs.example/4",
                "descriptionPlain": "Own analytics roadmap for product platform.",
            }
        ]
    }
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == ""

    fit = score_job(jobs[0], load_target_profile())
    assert "Unknown location" in fit.red_flags
