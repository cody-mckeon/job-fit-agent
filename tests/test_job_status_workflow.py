from pathlib import Path


def test_repository_dispatch_workflow_exists():
    assert Path(".github/workflows/job-status-command.yml").exists()


def test_workflow_listens_for_job_status_command():
    text = Path(".github/workflows/job-status-command.yml").read_text()
    assert "repository_dispatch:" in text
    assert "types: [job_status_command]" in text


def test_workflow_reads_repository_dispatch_command_text():
    text = Path(".github/workflows/job-status-command.yml").read_text()
    assert "github.event.client_payload.command_text" in text


def test_workflow_supports_manual_command_text():
    text = Path(".github/workflows/job-status-command.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "command_text:" in text
    assert "inputs.command_text" in text


def test_worker_validates_telegram_secret_header():
    text = Path("ops/telegram-worker/worker.js").read_text()
    assert "X-Telegram-Bot-Api-Secret-Token" in text


def test_worker_validates_allowed_chat_id():
    text = Path("ops/telegram-worker/worker.js").read_text()
    assert "TELEGRAM_ALLOWED_CHAT_ID" in text
    assert "chatId" in text


def test_worker_calls_github_dispatches_api():
    text = Path("ops/telegram-worker/worker.js").read_text()
    assert "https://api.github.com/repos/" in text
    assert "/dispatches" in text


def test_worker_uses_job_status_command_event_type():
    text = Path("ops/telegram-worker/worker.js").read_text()
    assert 'event_type: "job_status_command"' in text
