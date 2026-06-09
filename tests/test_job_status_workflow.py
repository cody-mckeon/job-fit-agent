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


def test_worker_allows_mobile_alias_and_stable_identifiers():
    text = Path("ops/telegram-worker/worker.js").read_text()
    assert "JOB_IDENTIFIER" in text
    assert ":._~/?#@!%&+=,-" in text


def test_workflow_commits_application_status_after_successful_command():
    text = Path(".github/workflows/job-status-command.yml").read_text()
    assert "git add data/application_status.json" in text
    assert "git commit -m \"Update application status\"" in text


def test_workflow_does_not_require_jobs_sqlite_to_change():
    text = Path(".github/workflows/job-status-command.yml").read_text()
    assert "git add data/jobs.sqlite" not in text
    assert "data/jobs.sqlite is not tracked" not in text


def test_process_telegram_commands_workflow_exists():
    assert Path(".github/workflows/process-telegram-commands.yml").exists()


def test_process_telegram_commands_workflow_polls_and_commits_data_files():
    text = Path(".github/workflows/process-telegram-commands.yml").read_text()
    assert "workflow_dispatch:" in text
    assert "cron:" in text
    assert "contents: write" in text
    assert "python -m job_fit_agent.main process-telegram-updates" in text
    assert "TELEGRAM_BOT_TOKEN" in text
    assert "TELEGRAM_CHAT_ID" in text
    assert "git add data/application_status.json data/company_application_blocks.json data/telegram_processed_updates.json" in text
    assert 'git commit -m "Process Telegram status commands"' in text
