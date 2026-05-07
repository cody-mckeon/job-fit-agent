from unittest.mock import Mock

import requests

from job_fit_agent.collectors.lever import LeverCollector


def test_fetch_jobs_parses_lever_response(monkeypatch):
    mock_response = Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = [
        {
            "text": "Product Manager",
            "hostedUrl": "https://jobs.example/lever-1",
            "categories": {
                "location": "Remote (US)",
                "commitment": "Full-time",
                "department": "Product",
                "team": "Platform",
            },
            "descriptionPlain": "Lead roadmap execution",
        }
    ]
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = LeverCollector().fetch_jobs("ramp")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "lever"
    assert job.company == "ramp"
    assert job.title == "Product Manager"
    assert job.location == "Remote (US)"
    assert job.workplace_type == "Full-time"
    assert job.department == "Product"
    assert job.team == "Platform"
    assert job.url == "https://jobs.example/lever-1"
    assert job.description == "Lead roadmap execution"


def test_validate_company_token_handles_404(monkeypatch):
    response = Mock()
    response.status_code = 404
    err = requests.RequestException("not found")
    err.response = response

    def _raise(*args, **kwargs):
        raise err

    monkeypatch.setattr(requests, "get", _raise)

    collector = LeverCollector()
    assert collector.validate_company_token("badtoken") is False
