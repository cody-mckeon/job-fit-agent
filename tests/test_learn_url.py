from pathlib import Path

from job_fit_agent.config import load_discovery_queue
from job_fit_agent.main import ParsedJobUrl, learn_url, main, parse_job_url
from job_fit_agent.models import JobPosting


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
