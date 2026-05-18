from unittest.mock import Mock

import requests

from job_fit_agent.collectors.lever import LeverCollector, normalize_lever_job_url


BOARD_HTML = """
<html>
  <body>
    <div class="postings-group">
      <h3 class="posting-category-title large-category-label department">Product</h3>
      <div class="posting" data-qa="posting">
        <a class="posting-title" href="/ramp/abc123?lever-source=LinkedIn#apply">
          <h5 data-qa="posting-name">Senior Product Manager</h5>
          <div class="posting-categories">
            <span class="sort-by-location posting-category location">Remote (US)</span>
            <span class="sort-by-team posting-category team">Platform</span>
            <span class="sort-by-commitment posting-category commitment">Full-time</span>
          </div>
        </a>
      </div>
    </div>
  </body>
</html>
"""

DETAIL_HTML = """
<html>
  <body>
    <div class="posting-page">
      <div class="posting-headline">Senior Product Manager Remote (US)</div>
      <div class="section-wrapper">
        <p>Lead roadmap execution for AI workflows.</p>
      </div>
    </div>
  </body>
</html>
"""


def _response(text: str):
    response = Mock()
    response.text = text
    response.raise_for_status.return_value = None
    return response


def test_fetch_jobs_parses_mocked_lever_board_html(monkeypatch):
    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        if url == "https://jobs.lever.co/ramp":
            return _response(BOARD_HTML)
        return _response(DETAIL_HTML)

    monkeypatch.setattr(requests, "get", fake_get)

    jobs = LeverCollector().fetch_jobs("ramp")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "lever"
    assert job.company == "ramp"
    assert job.title == "Senior Product Manager"
    assert job.location == "Remote (US)"
    assert job.location_raw == "Remote (US)"
    assert job.workplace_type == "Full-time"
    assert job.department == "Product"
    assert job.team == "Platform"
    assert job.url == "https://jobs.lever.co/ramp/abc123"
    assert job.description == "Lead roadmap execution for AI workflows."
    assert calls == ["https://jobs.lever.co/ramp", "https://jobs.lever.co/ramp/abc123"]


def test_normalize_lever_job_url_strips_tracking_values():
    assert (
        normalize_lever_job_url("https://jobs.lever.co/ramp/abc123?lever-source=LinkedIn#apply")
        == "https://jobs.lever.co/ramp/abc123"
    )
    assert normalize_lever_job_url("/ramp/abc123/", company="ramp") == "https://jobs.lever.co/ramp/abc123"


def test_fetch_jobs_supports_legacy_lever_json_payload(monkeypatch):
    mock_response = Mock()
    mock_response.text = ""
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = [
        {
            "text": "Product Manager",
            "hostedUrl": "https://jobs.lever.co/ramp/lever-1?utm=1",
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
    assert job.url == "https://jobs.lever.co/ramp/lever-1"
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
