from pathlib import Path


def test_scheduled_workflow_uses_quiet_if_empty():
    workflow = Path(".github/workflows/process-telegram-commands.yml").read_text()

    assert "github.event_name" in workflow
    assert "python -m job_fit_agent.main process-telegram-updates --quiet-if-empty" in workflow
