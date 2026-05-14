from unittest.mock import Mock

import requests

from job_fit_agent.collectors.ashby import (
    AshbyCollector,
    extract_ashby_app_data_metadata,
    extract_ashby_hydration_data,
    extract_ashby_json_ld_metadata,
)
from job_fit_agent.config import load_target_profile
from job_fit_agent.scoring import normalize_location, score_job


def _mock_response(payload: dict, text: str = "") -> Mock:
    response = Mock()
    response.status_code = 200
    response.text = text
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def test_fetch_jobs_parses_ashby_response(monkeypatch):
    mock_response = _mock_response({
        "jobs": [
            {
                "title": "Software Engineer",
                "jobUrl": "https://jobs.example/1",
                "location": {"locationName": "Remote US"},
                "descriptionPlain": "Build AI products",
            }
        ]
    })
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source == "ashby"
    assert job.company == "anthropic"
    assert job.title == "Software Engineer"
    assert job.workplace_type == ""
    assert job.department == ""
    assert job.team == ""


def test_fetch_jobs_handles_failed_request(monkeypatch):
    def _raise(*args, **kwargs):
        raise requests.RequestException("boom")

    monkeypatch.setattr(requests, "get", _raise)
    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs == []


def test_fetch_jobs_parses_location_name_field(monkeypatch):
    mock_response = _mock_response({
        "jobs": [
            {
                "title": "PM",
                "jobUrl": "https://jobs.example/2",
                "locationName": "San Francisco, CA",
                "descriptionPlain": "desc",
            }
        ]
    })
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "San Francisco, CA"


def test_fetch_jobs_maps_remote_from_workplace_type(monkeypatch):
    mock_response = _mock_response({
        "jobs": [
            {
                "title": "PM",
                "jobUrl": "https://jobs.example/3",
                "workplaceType": "remote",
                "locationName": "United States",
                "descriptionPlain": "desc",
            }
        ]
    })
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "United States"
    assert jobs[0].workplace_type == "Remote"


def test_missing_location_maps_to_empty_and_scores_unknown(monkeypatch):
    mock_response = _mock_response({
        "jobs": [
            {
                "title": "Product Manager",
                "jobUrl": "https://jobs.example/4",
                "descriptionPlain": "Own analytics roadmap for product platform.",
            }
        ]
    })
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == ""

    fit = score_job(jobs[0], load_target_profile())
    assert jobs[0].geographic_eligibility == "review"


def test_fetch_jobs_separates_metadata_fields(monkeypatch):
    mock_response = _mock_response({
        "jobs": [
            {
                "title": "PMM",
                "jobUrl": "https://jobs.example/5",
                "locationName": "Remote US",
                "workplaceType": "hybrid",
                "departmentName": "GTM",
                "team": {"name": "Marketing"},
                "descriptionPlain": "desc",
            }
        ]
    })
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: mock_response)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    job = jobs[0]
    assert job.location == "Remote US"
    assert job.workplace_type == "Hybrid"
    assert job.department == "GTM"
    assert job.team == "Marketing"


def test_fetch_jobs_location_fallback_from_job_page(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Senior Product Manager",
                "jobUrl": "https://jobs.example/foster-city",
                "locationName": "United States",
                "descriptionPlain": "Own AI roadmap.",
            }
        ]
    })

    html_response = _mock_response(
        {},
        text="""
    <div>
      <h2>Location</h2>
      <p>Foster City, CA (Hybrid) In office M,W,F</p>
    </div>
    """,
    )

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "Foster City, CA (Hybrid) In office M,W,F"
    assert jobs[0].workplace_type == "Hybrid"

def test_fetch_jobs_sidebar_parses_location_type(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Senior Product Manager",
                "jobUrl": "https://jobs.example/sidebar-meta",
                "locationName": "United States",
                "descriptionPlain": "Own AI roadmap.",
            }
        ]
    })

    html_response = _mock_response(
        {},
        text="""
    <aside>
      <div><h3>Location</h3><p>Foster City, CA (Hybrid) In office M,W,F</p></div>
      <div><h3>Location Type</h3><p>Hybrid</p></div>
      <div><h3>Department</h3><p>Product</p></div>
    </aside>
    """,
    )

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "Foster City, CA (Hybrid) In office M,W,F"
    assert jobs[0].workplace_type == "Hybrid"
    assert jobs[0].department == "Product"


def test_fetch_jobs_sidebar_location_prevents_location_not_specified(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Product Manager",
                "jobUrl": "https://jobs.example/sidebar-location",
                "descriptionPlain": "Own analytics roadmap.",
            }
        ]
    })

    html_response = _mock_response(
        {},
        text="""
    <section>
      <div><span>Location</span><span>Foster City, CA (Hybrid) In office M,W,F</span></div>
      <div><span>Location Type</span><span>Hybrid</span></div>
    </section>
    """,
    )

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)

    jobs = AshbyCollector().fetch_jobs("anthropic")
    fit = score_job(jobs[0], load_target_profile())
    assert "Location not specified" not in fit.red_flags

from job_fit_agent.collectors.ashby import parse_ashby_sidebar_metadata


def test_parse_sidebar_remote_usa_location() -> None:
    metadata = parse_ashby_sidebar_metadata("""
    <aside>
      <div>Location</div>
      <div>Remote USA</div>
      <div>Location Type</div>
      <div>Remote</div>
      <div>Department</div>
      <div>Engineering &amp; Product</div>
    </aside>
    """)
    assert metadata["Location"] == "Remote USA"
    assert metadata["Location Type"] == "Remote"
    assert metadata["Department"] == "Engineering & Product"


def test_parse_sidebar_foster_city_hybrid_location() -> None:
    metadata = parse_ashby_sidebar_metadata("""
    <div>Location</div>
    <div>Foster City, CA (Hybrid)</div>
    """)
    assert metadata["Location"] == "Foster City, CA (Hybrid)"


def test_product_engineer_remote_usa_sidebar_location(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Product Engineer",
                "jobUrl": "https://jobs.example/product-eng-remote",
                "locationName": "United States",
                "descriptionPlain": "Build products",
            }
        ]
    })
    html_response = _mock_response({}, text="<div>Location</div><div>Remote USA</div><div>Location Type</div><div>Remote</div>")

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)
    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "Remote USA"
from pathlib import Path


def _fixture_html(name: str) -> str:
    return Path(f"tests/fixtures/ashby/{name}").read_text(encoding="utf-8")


def test_parse_sidebar_fixture_replit_foster_city_hybrid() -> None:
    metadata = parse_ashby_sidebar_metadata(_fixture_html("replit_sidebar.html"))
    assert metadata["Location"] == "Foster City, CA"
    assert metadata["Location Type"] == "Hybrid"


def test_parse_sidebar_fixture_linear_remote_usa() -> None:
    metadata = parse_ashby_sidebar_metadata(_fixture_html("linear_sidebar.html"))
    assert metadata["Location"] == "Remote USA"


def test_parse_sidebar_fixture_perplexity_multi_country() -> None:
    metadata = parse_ashby_sidebar_metadata(_fixture_html("perplexity_sidebar.html"))
    assert metadata["Location"] == "Mexico / Argentina / Peru"


def test_parse_sidebar_fixture_department_and_location_type() -> None:
    metadata = parse_ashby_sidebar_metadata(_fixture_html("elevenlabs_sidebar.html"))
    assert metadata["Department"] == "Growth"
    assert metadata["Location Type"] == "Remote"


def test_sidebar_location_not_blank_when_sidebar_exists(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Product Manager",
                "jobUrl": "https://jobs.example/sidebar-known",
                "locationName": "United States",
                "descriptionPlain": "Own roadmap.",
            }
        ]
    })
    html_response = _mock_response({}, text=_fixture_html("linear_sidebar.html"))

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)
    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location != ""
    assert jobs[0].location == "Remote USA"


def test_extract_ashby_hydration_data_from_next_data_fixture() -> None:
    metadata = extract_ashby_hydration_data(_fixture_html("with_next_data.html"))
    assert metadata["Location"] == "Remote USA"
    assert metadata["Location Type"] == "Remote"
    assert metadata["Department"] == "Growth"
    assert metadata["Team"] == "Platform"


def test_fetch_jobs_prefers_hydration_metadata_over_sidebar(monkeypatch):
    api_response = _mock_response({
        "jobs": [
            {
                "title": "Senior Product Manager",
                "jobUrl": "https://jobs.example/next-data",
                "locationName": "United States",
                "descriptionPlain": "Own roadmap.",
            }
        ]
    })
    html_response = _mock_response({}, text=_fixture_html("with_next_data.html"))

    def _mock_get(url, timeout):
        if "posting-api/job-board" in url:
            return api_response
        return html_response

    monkeypatch.setattr(requests, "get", _mock_get)
    jobs = AshbyCollector().fetch_jobs("anthropic")
    assert jobs[0].location == "Remote USA"
    assert jobs[0].workplace_type == "Remote"
    assert jobs[0].department == "Growth"
    assert jobs[0].team == "Platform"


ASHBY_REPLIT_HTML = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","jobLocation":{"address":{"addressLocality":"Foster City","addressRegion":"California","addressCountry":"USA"}},"jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"name":"USA"}}
</script>
<script>
window.__appData = {"posting":{"locationName":"Foster City, CA","locationExternalName":"Foster City, CA","address":{"postalAddress":{"addressLocality":"Foster City","addressRegion":"California","addressCountry":"USA"}},"isRemote":true,"workplaceType":"Hybrid","departmentName":"Product Management","teamName":"Product Management"}};
</script>
<p>Are you able to work from our Foster City, CA HQ 3 days per week?</p>
"""


def test_extract_ashby_app_data_and_json_ld_metadata() -> None:
    app = extract_ashby_app_data_metadata(ASHBY_REPLIT_HTML)
    json_ld = extract_ashby_json_ld_metadata(ASHBY_REPLIT_HTML)
    assert app["Location"] == "Foster City, CA"
    assert app["Location Type"] == "Hybrid"
    assert app["city"] == "Foster City"
    assert json_ld["Location Type"] == "TELECOMMUTE"


def test_app_data_overrides_json_ld_workplace_type(monkeypatch):
    api_response = _mock_response({"jobs": [{"title": "PM", "jobUrl": "https://jobs.example/replit", "descriptionPlain": "desc"}]})
    html_response = _mock_response({}, text=ASHBY_REPLIT_HTML)

    def _mock_get(url, timeout):
        return api_response if "posting-api/job-board" in url else html_response

    monkeypatch.setattr(requests, "get", _mock_get)
    job = AshbyCollector().fetch_jobs("replit")[0]
    assert job.location == "Foster City, CA"
    assert job.workplace_type == "Hybrid"
    fit = score_job(job, load_target_profile())
    assert job.geographic_eligibility == "ineligible"
    assert fit.viability_level in {"skip", "stretch"}


def test_json_ld_fallback_when_app_data_missing(monkeypatch):
    api_response = _mock_response({"jobs": [{"title": "PM", "jobUrl": "https://jobs.example/jld", "descriptionPlain": "desc"}]})
    html_response = _mock_response({}, text="""
    <script type="application/ld+json">
    {"@type":"JobPosting","jobLocation":{"address":{"addressLocality":"Foster City","addressRegion":"CA","addressCountry":"USA"}},"jobLocationType":"TELECOMMUTE","applicantLocationRequirements":{"name":"USA"}}
    </script>
    """)
    monkeypatch.setattr(requests, "get", lambda url, timeout: api_response if "posting-api/job-board" in url else html_response)
    job = AshbyCollector().fetch_jobs("replit")[0]
    assert job.location == "Foster City, CA"


def test_location_viability_cases() -> None:
    remote = normalize_location("Remote USA", "Remote")
    assert remote["geographic_eligibility"] == "eligible"

    multi_country = normalize_location("Mexico; Argentina; Peru", "Remote")
    assert multi_country["geographic_eligibility"] == "ineligible"

    hybrid_fc = normalize_location("Foster City, CA", "Hybrid")
    assert hybrid_fc["normalized_city"] == "Foster City"
    assert hybrid_fc["normalized_state"] in {"CA", "California"}
    assert hybrid_fc["normalized_country"] in {"US", "USA"}
    assert hybrid_fc["normalized_location_type"] == "hybrid"
    assert hybrid_fc["geographic_eligibility"] == "ineligible"
