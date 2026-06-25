from pathlib import Path

from job_fit_agent.config import load_discovery_queue
from job_fit_agent.main import ParsedJobUrl, learn_url, main, parse_job_url
from job_fit_agent.models import JobPosting
from job_fit_agent.repository import get_job_by_url


def test_parse_ashby_url_company() -> None:
    parsed = parse_job_url("https://jobs.ashbyhq.com/scrunch/abc123")
    assert parsed == ParsedJobUrl(source="ashby", company="scrunch", job_id="abc123", original_url="https://jobs.ashbyhq.com/scrunch/abc123")


def test_parse_greenhouse_url_company() -> None:
    parsed = parse_job_url("https://boards.greenhouse.io/openai/jobs/12345?gh_jid=12345")
    assert parsed.source == "greenhouse"
    assert parsed.company == "openai"
    assert parsed.job_id == "12345"




def test_parse_greenhouse_job_boards_url_company() -> None:
    parsed = parse_job_url("https://job-boards.greenhouse.io/robotsandpencils/jobs/5227395008")
    assert parsed.source == "greenhouse"
    assert parsed.company == "robotsandpencils"
    assert parsed.job_id == "5227395008"

def test_parse_lever_url_company() -> None:
    parsed = parse_job_url("https://jobs.lever.co/ramp/xyz987")
    assert parsed.source == "lever"
    assert parsed.company == "ramp"
    assert parsed.job_id == "xyz987"


def test_invalid_url_returns_clear_error(capsys) -> None:
    main(["learn-url", "https://example.com/nope"])
    output = capsys.readouterr().out
    assert "Unsupported job URL" in output


def test_learn_url_adds_company_to_discovery_queue(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    queue_path.write_text("ashby:\n  []\ngreenhouse:\n  []\nlever:\n  []\n", encoding="utf-8")

    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.score_job", lambda job, profile: type("Fit", (), {"classification": "high_fit"})())
    monkeypatch.setattr("job_fit_agent.main.upsert_job", lambda job, fit: None)
    monkeypatch.setattr("job_fit_agent.main.AppConfig", lambda: type("Cfg", (), {"enable_lever": True})())
    monkeypatch.setattr("job_fit_agent.main.load_discovery_queue", lambda: load_discovery_queue(queue_path))
    monkeypatch.setattr("job_fit_agent.main.save_discovery_queue", lambda queue: __import__("job_fit_agent.config").config.save_discovery_queue(queue, queue_path))

    class Stub:
        def fetch_jobs(self, company: str):
            return [JobPosting(source="ashby", company=company, title="PM", location="Remote", url="https://x/1", description="ai")]

    monkeypatch.setattr("job_fit_agent.main._build_enabled_collectors", lambda config: {"ashby": Stub()})

    learn_url("https://jobs.ashbyhq.com/scrunch/abc123")
    queue = load_discovery_queue(queue_path)
    assert queue.ashby == ["scrunch"]


def test_learn_url_does_not_duplicate_company(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    queue_path.write_text("ashby:\n  - scrunch\ngreenhouse:\n  []\nlever:\n  []\n", encoding="utf-8")
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.score_job", lambda job, profile: type("Fit", (), {"classification": "near_fit"})())
    monkeypatch.setattr("job_fit_agent.main.upsert_job", lambda job, fit: None)
    monkeypatch.setattr("job_fit_agent.main.AppConfig", lambda: type("Cfg", (), {"enable_lever": True})())
    monkeypatch.setattr("job_fit_agent.main.load_discovery_queue", lambda: load_discovery_queue(queue_path))
    monkeypatch.setattr("job_fit_agent.main.save_discovery_queue", lambda queue: __import__("job_fit_agent.config").config.save_discovery_queue(queue, queue_path))

    class Stub:
        def fetch_jobs(self, company: str):
            return [JobPosting(source="ashby", company=company, title="PM", location="Remote", url="https://x/1", description="ai")]

    monkeypatch.setattr("job_fit_agent.main._build_enabled_collectors", lambda config: {"ashby": Stub()})

    learn_url("https://jobs.ashbyhq.com/scrunch/abc123")
    queue = load_discovery_queue(queue_path)
    assert queue.ashby == ["scrunch"]


def test_learn_url_fetches_scores_and_persists(monkeypatch) -> None:
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    calls = {"fetched": 0, "scored": 0, "upserted": 0}

    def _score(job, profile):
        calls["scored"] += 1
        return type("Fit", (), {"classification": "high_fit"})()

    def _upsert(job, fit):
        calls["upserted"] += 1

    monkeypatch.setattr("job_fit_agent.main.score_job", _score)
    monkeypatch.setattr("job_fit_agent.main.upsert_job", _upsert)
    monkeypatch.setattr("job_fit_agent.main.load_discovery_queue", lambda: type("Queue", (), {"ashby": [], "greenhouse": [], "lever": []})())
    monkeypatch.setattr("job_fit_agent.main.save_discovery_queue", lambda queue: None)
    monkeypatch.setattr("job_fit_agent.main.AppConfig", lambda: type("Cfg", (), {"enable_lever": True})())

    class Stub:
        def fetch_jobs(self, company: str):
            calls["fetched"] += 1
            return [JobPosting(source="ashby", company=company, title="PM", location="Remote", url="https://x/1", description="ai")]

    monkeypatch.setattr("job_fit_agent.main._build_enabled_collectors", lambda config: {"ashby": Stub()})

    learn_url("https://jobs.ashbyhq.com/scrunch/abc123")
    assert calls == {"fetched": 1, "scored": 1, "upserted": 1}


def test_learn_url_accepts_greenhouse_job_boards_url(monkeypatch, tmp_path: Path, capsys) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    queue_path.write_text("ashby:\n  []\ngreenhouse:\n  []\nlever:\n  []\n", encoding="utf-8")

    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.score_job", lambda job, profile: type("Fit", (), {"classification": "high_fit"})())
    monkeypatch.setattr("job_fit_agent.main.upsert_job", lambda job, fit: None)
    monkeypatch.setattr("job_fit_agent.main.AppConfig", lambda: type("Cfg", (), {"enable_lever": True})())
    monkeypatch.setattr("job_fit_agent.main.load_discovery_queue", lambda: load_discovery_queue(queue_path))
    monkeypatch.setattr("job_fit_agent.main.save_discovery_queue", lambda queue: __import__("job_fit_agent.config").config.save_discovery_queue(queue, queue_path))

    class Stub:
        def fetch_jobs(self, company: str):
            return [JobPosting(source="greenhouse", company=company, title="PM", location="Remote", url="https://x/1", description="ai")]

    monkeypatch.setattr("job_fit_agent.main._build_enabled_collectors", lambda config: {"greenhouse": Stub()})

    learn_url("https://job-boards.greenhouse.io/robotsandpencils/jobs/5227395008")
    output = capsys.readouterr().out
    assert "parsed source: greenhouse" in output
    assert "parsed company: robotsandpencils" in output
    assert "parsed job id: 5227395008" in output

    queue = load_discovery_queue(queue_path)
    assert queue.greenhouse == ["robotsandpencils"]

WORKDAY_URL = "https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs/job/Austin-TX---Virtual/Product-Manager--Digital-Buying---Selling_R26_0000002533?source=Monster.com"


def test_parse_workday_url_lennar_fields() -> None:
    parsed = parse_job_url(WORKDAY_URL)
    assert parsed.source == "workday"
    assert parsed.company == "lennar"
    assert parsed.tenant == "lennar"
    assert parsed.site == "Lennar_Jobs"
    assert parsed.location_slug == "Austin-TX---Virtual"
    assert parsed.job_slug == "Product-Manager--Digital-Buying---Selling"
    assert parsed.job_id == "R26_0000002533"
    assert parsed.canonical_url == "https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs/job/Austin-TX---Virtual/Product-Manager--Digital-Buying---Selling_R26_0000002533"


def test_workday_fallback_normalizes_title_and_location() -> None:
    parsed = parse_job_url(WORKDAY_URL)
    job = __import__("job_fit_agent.main").main.build_workday_fallback_job(parsed)
    assert job.title == "Product Manager, Digital Buying & Selling"
    assert job.location == "Austin, TX - Virtual"
    assert job.source == "workday"
    assert job.url == parsed.canonical_url


def test_learn_url_accepts_workday_and_creates_fallback(monkeypatch, capsys) -> None:
    stored = {}
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *a, **k: None)
    monkeypatch.setattr("job_fit_agent.main.upsert_job", lambda job, fit: stored.update({"job": job, "fit": fit}))

    learn_url(WORKDAY_URL)
    output = capsys.readouterr().out
    assert '"success": true' in output
    assert '"source": "workday"' in output
    assert '"stable_job_key": "workday:lennar:R26_0000002533"' in output
    assert stored["job"].title == "Product Manager, Digital Buying & Selling"
    assert stored["fit"].classification == "needs_review"


def test_learn_url_workday_description_file_stores_description(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    description_file = tmp_path / "workday_description.txt"
    description_file.write_text(
        "Lead digital buying and selling product strategy with AI workflow automation, product analytics, experimentation, and cross-functional delivery for homebuying experiences.",
        encoding="utf-8",
    )
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *a, **k: None)
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr(
        "job_fit_agent.main.score_job",
        lambda job, profile: __import__("job_fit_agent.models").models.FitScore(
            total_score=88,
            classification="high_fit",
            role_family="product_management",
            viability_score=80,
            viability_level="apply_now",
        ),
    )

    learn_url(WORKDAY_URL, description_file=str(description_file))
    output = capsys.readouterr().out
    row = get_job_by_url("https://lennar.wd1.myworkdayjobs.com/Lennar_Jobs/job/Austin-TX---Virtual/Product-Manager--Digital-Buying---Selling_R26_0000002533")

    assert row is not None
    assert len(row["notes"]) > 0
    assert row["classification"] != "needs_review"
    assert '"description": true' in output
    assert "Description supplied manually from description file." in output


def test_prep_url_workday_returns_warning_package_when_review(monkeypatch, capsys) -> None:
    row = None
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.load_target_profile", lambda: object())
    monkeypatch.setattr("job_fit_agent.main._fetch_direct_job_page", lambda *a, **k: None)

    def _upsert(job, fit):
        nonlocal row
        row = {"id": 22, "source": job.source, "company": job.company, "title": job.title, "location": job.location, "url": job.url, "classification": fit.classification, "geographic_eligibility": "review", "viability_level": "review"}

    monkeypatch.setattr("job_fit_agent.main.upsert_job", _upsert)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_url", lambda url: row if row and row["url"] == url else None)
    result = __import__("job_fit_agent.main").main.prep_url(WORKDAY_URL)
    assert result["source"] == "workday"
    assert result["stable_job_key"] == "workday:lennar:R26_0000002533"
    assert "requires review" in result["warning"]
    assert "Workday" in capsys.readouterr().out
