from pathlib import Path

from job_fit_agent.config import load_company_watchlist, load_discovery_queue
from job_fit_agent.main import main


def _write_yaml(path: Path, ashby: list[str], greenhouse: list[str] | None = None, lever: list[str] | None = None) -> None:
    greenhouse = greenhouse or []
    lever = lever or []
    lines = ["ashby:"]
    lines.extend([f"  - {company}" for company in ashby] or ["  []"])
    lines.append("greenhouse:")
    lines.extend([f"  - {company}" for company in greenhouse] or ["  []"])
    lines.append("lever:")
    lines.extend([f"  - {company}" for company in lever] or ["  []"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _patch_config_paths(monkeypatch, queue_path: Path, watchlist_path: Path) -> None:
    monkeypatch.setattr("job_fit_agent.main.load_discovery_queue", lambda: load_discovery_queue(queue_path))
    monkeypatch.setattr("job_fit_agent.main.save_discovery_queue", lambda queue: __import__("job_fit_agent.config").config.save_discovery_queue(queue, queue_path))
    monkeypatch.setattr("job_fit_agent.main.load_company_watchlist", lambda: load_company_watchlist(watchlist_path))
    monkeypatch.setattr("job_fit_agent.main.save_company_watchlist", lambda watchlist: __import__("job_fit_agent.config").config.save_company_watchlist(watchlist, watchlist_path))


def test_promotes_ashby_company(monkeypatch, tmp_path: Path, capsys) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    watchlist_path = tmp_path / "company_watchlist.yaml"
    _write_yaml(queue_path, ashby=["scrunch"])
    _write_yaml(watchlist_path, ashby=["airtable"])
    _patch_config_paths(monkeypatch, queue_path, watchlist_path)

    main(["promote-discovery", "ashby", "scrunch"])

    output = capsys.readouterr().out
    watchlist = load_company_watchlist(watchlist_path)
    assert "Promoted ashby/scrunch to company watchlist" in output
    assert watchlist.ashby == ["airtable", "scrunch"]


def test_removes_company_from_discovery_queue(monkeypatch, tmp_path: Path) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    watchlist_path = tmp_path / "company_watchlist.yaml"
    _write_yaml(queue_path, ashby=["scrunch", "zeal"])
    _write_yaml(watchlist_path, ashby=[])
    _patch_config_paths(monkeypatch, queue_path, watchlist_path)

    main(["promote-discovery", "ashby", "scrunch"])

    queue = load_discovery_queue(queue_path)
    assert queue.ashby == ["zeal"]


def test_does_not_duplicate_company_in_watchlist(monkeypatch, tmp_path: Path, capsys) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    watchlist_path = tmp_path / "company_watchlist.yaml"
    _write_yaml(queue_path, ashby=["scrunch"])
    _write_yaml(watchlist_path, ashby=["scrunch"])
    _patch_config_paths(monkeypatch, queue_path, watchlist_path)

    main(["promote-discovery", "ashby", "scrunch"])

    output = capsys.readouterr().out
    watchlist = load_company_watchlist(watchlist_path)
    queue = load_discovery_queue(queue_path)
    assert "ashby/scrunch already exists in company watchlist" in output
    assert watchlist.ashby == ["scrunch"]
    assert queue.ashby == []


def test_promote_discovery_handles_invalid_source(capsys) -> None:
    main(["promote-discovery", "workday", "scrunch"])
    output = capsys.readouterr().out
    assert "Invalid source 'workday'" in output


def test_promote_discovery_handles_missing_company(monkeypatch, tmp_path: Path, capsys) -> None:
    queue_path = tmp_path / "discovery_queue.yaml"
    watchlist_path = tmp_path / "company_watchlist.yaml"
    _write_yaml(queue_path, ashby=["otherco"])
    _write_yaml(watchlist_path, ashby=[])
    _patch_config_paths(monkeypatch, queue_path, watchlist_path)

    main(["promote-discovery", "ashby", "scrunch"])

    output = capsys.readouterr().out
    assert "was not found in discovery queue" in output
