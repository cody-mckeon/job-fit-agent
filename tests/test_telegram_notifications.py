from __future__ import annotations

from job_fit_agent.config import NotificationConfig, TelegramConfig
from job_fit_agent.main import format_high_fit_notification, run_pipeline
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import UpsertResult
from job_fit_agent.notifications.telegram import send_message


class StubCollector:
    def __init__(self, jobs_by_company):
        self.jobs_by_company = jobs_by_company

    def fetch_jobs(self, company: str):
        return self.jobs_by_company.get(company, [])


def _job(title: str, url: str, description: str = "AI roadmap") -> JobPosting:
    return JobPosting(
        source="greenhouse",
        company="openai",
        title=title,
        location="Remote US",
        url=url,
        description=description,
    )


def test_notification_formatting() -> None:
    job = _job("Product Manager AI", "https://example.com/pm-ai")
    fit = FitScore(total_score=92, classification="high_fit", role_family="product")

    message = format_high_fit_notification(job, fit)

    assert message == "\n".join(
        [
            "[HIGH FIT JOB]",
            "openai",
            "Product Manager AI",
            "Score: 92",
            "Source: greenhouse",
            "https://example.com/pm-ai",
        ]
    )


def test_disabled_notifications_do_nothing(monkeypatch) -> None:
    monkeypatch.setattr(
        "job_fit_agent.notifications.telegram.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(enabled=False, bot_token="token", chat_id="id")),
    )

    called = {"value": False}

    def _should_not_call(*args, **kwargs):
        called["value"] = True
        raise AssertionError("urlopen should not be called when notifications are disabled")

    monkeypatch.setattr("job_fit_agent.notifications.telegram.urlopen", _should_not_call)

    send_message("hello")
    assert called["value"] is False


def test_only_new_high_fit_jobs_trigger_notifications(monkeypatch) -> None:
    high = _job("Product Manager AI", "https://example.com/high")
    near = _job("Technical Program Manager", "https://example.com/near", description="program delivery")
    low = _job("Software Engineer", "https://example.com/low", description="backend systems")

    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr(
        "job_fit_agent.main.load_notification_config",
        lambda: NotificationConfig(telegram=TelegramConfig(enabled=True, bot_token="token", chat_id="id")),
    )
    monkeypatch.setattr("job_fit_agent.main.GreenhouseCollector", lambda: StubCollector({"openai": [high, near, low]}))
    monkeypatch.setattr("job_fit_agent.main.AshbyCollector", lambda: StubCollector({}))
    monkeypatch.setattr("job_fit_agent.main.resolve_companies", lambda source="greenhouse": ["openai"] if source == "greenhouse" else [])

    def _upsert(job, fit):
        if job.url.endswith("high"):
            return UpsertResult(is_new=True, updated=False, skipped_duplicate=False)
        if job.url.endswith("near"):
            return UpsertResult(is_new=True, updated=False, skipped_duplicate=False)
        return UpsertResult(is_new=False, updated=False, skipped_duplicate=True)

    monkeypatch.setattr("job_fit_agent.main.upsert_job", _upsert)

    sent: list[str] = []
    monkeypatch.setattr("job_fit_agent.main.send_message", sent.append)

    run_pipeline()

    assert len(sent) == 1
    assert sent[0].startswith("[HIGH FIT JOB]")
    assert "Product Manager AI" in sent[0]
    assert "Technical Program Manager" not in sent[0]
