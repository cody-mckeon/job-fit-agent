import json

from job_fit_agent import main as job_main
from job_fit_agent.main import main
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_job_by_id, get_job_by_url, initialize, upsert_job
from job_fit_agent.telegram_commands import parse_telegram_command


def _job(url: str, title: str = "Product Manager") -> JobPosting:
    return JobPosting(
        source="ashby",
        company="linear",
        title=title,
        location="Remote US",
        location_raw="Remote US",
        geographic_eligibility="eligible",
        url=url,
        description="AI workflow automation product systems",
    )


def _fit() -> FitScore:
    return FitScore(
        total_score=92,
        classification="high_fit",
        role_family="product",
        viability_score=91,
        viability_level="apply_now",
        reasons=["strong product fit"],
        red_flags=[],
    )


def _insert(url: str, title: str = "Product Manager") -> int:
    upsert_job(_job(url, title), _fit())
    row = get_job_by_url(url)
    assert row is not None
    return int(row["id"])


def test_parser_parses_applied_plain():
    parsed = parse_telegram_command("applied 19")
    assert parsed.as_dict() == {"action": "applied", "job_identifier": "19", "note": "", "job_id": 19}


def test_parser_parses_applied_slash():
    parsed = parse_telegram_command("/applied 19")
    assert parsed.action == "applied"
    assert parsed.job_id == 19


def test_parser_parses_mark_applied():
    parsed = parse_telegram_command("mark applied 19")
    assert parsed.action == "applied"
    assert parsed.job_id == 19


def test_parser_parses_skip_with_note():
    parsed = parse_telegram_command("skip 19 Not US eligible")
    assert parsed.as_dict() == {"action": "skip", "job_identifier": "19", "note": "Not US eligible", "job_id": 19}


def test_parser_parses_slash_skip_with_note():
    parsed = parse_telegram_command("/skip 19 Not US eligible")
    assert parsed.action == "skip"
    assert parsed.note == "Not US eligible"


def test_parser_parses_save():
    parsed = parse_telegram_command("save 19")
    assert parsed.as_dict() == {"action": "save", "job_identifier": "19", "note": "", "job_id": 19}


def test_parser_rejects_missing_job_id():
    try:
        parse_telegram_command("applied")
    except ValueError as exc:
        assert "Missing job id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_parser_accepts_mobile_alias_identifier():
    parsed = parse_telegram_command("applied linear-product-manager")
    assert parsed.action == "applied"
    assert parsed.job_identifier == "linear-product-manager"
    assert parsed.job_id is None


def test_parser_rejects_unsupported_command():
    try:
        parse_telegram_command("delete 19")
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_parser_rejects_dangerous_shell_like_input():
    try:
        parse_telegram_command("applied 19; rm -rf /")
    except ValueError as exc:
        assert "shell-like" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_telegram_command_applied_updates_job_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/applied-command", "Product Manager")

    main(["telegram-command", f"applied {job_id}"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["company"] == "linear"
    assert result["title"] == "Product Manager"
    assert result["new_status"] == "applied"
    assert row is not None
    assert row["application_status"] == "applied"
    assert row["applied_at"]


def test_telegram_command_skip_updates_skipped_status_and_note(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/skip-command", "Forward Deployed Engineer")

    main(["telegram-command", f"skip {job_id} Not US eligible"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["new_status"] == "skipped"
    assert result["note"] == "Not US eligible"
    assert row is not None
    assert row["application_status"] == "skipped"
    assert row["skipped_at"]
    assert row["application_notes"] == "Not US eligible"


def test_telegram_command_save_updates_saved_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/save-command", "Product Manager")

    main(["telegram-command", f"save {job_id}"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["new_status"] == "saved"
    assert row is not None
    assert row["application_status"] == "saved"
    assert row["saved_at"]


def test_invalid_telegram_command_returns_json_failure(capsys):
    main(["telegram-command", "delete 19"])

    result = json.loads(capsys.readouterr().out)
    assert result["success"] is False
    assert "Unsupported" in result["message"]


def test_short_status_cli_commands_update_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    applied_id = _insert("https://jobs.ashbyhq.com/linear/short-applied", "Applied PM")
    skipped_id = _insert("https://jobs.ashbyhq.com/linear/short-skip", "Skip PM")
    saved_id = _insert("https://jobs.ashbyhq.com/linear/short-save", "Save PM")

    main(["applied", str(applied_id)])
    main(["skip", str(skipped_id), "Not", "US", "eligible"])
    main(["save", str(saved_id)])

    capsys.readouterr()
    applied = get_job_by_id(applied_id)
    skipped = get_job_by_id(skipped_id)
    saved = get_job_by_id(saved_id)
    assert applied is not None and applied["application_status"] == "applied"
    assert skipped is not None and skipped["application_status"] == "skipped"
    assert skipped["application_notes"] == "Not US eligible"
    assert saved is not None and saved["application_status"] == "saved"


def test_telegram_command_applied_mobile_alias_resolves_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/b7669c4b-eeca-421d-ba9a-d90203f6fcb2", "Product Manager")

    main(["telegram-command", "applied linear-product-manager"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["job_id"] == job_id
    assert row is not None and row["application_status"] == "applied"


def test_telegram_command_skip_mobile_alias_resolves_job_and_note(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/skip-mobile-alias", "Product Manager")

    main(["telegram-command", "skip linear-product-manager Not a fit"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["job_id"] == job_id
    assert result["note"] == "Not a fit"
    assert row is not None and row["application_status"] == "skipped"
    assert row["application_notes"] == "Not a fit"


def test_alias_collision_appends_short_hash_or_job_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    first_id = _insert("https://jobs.ashbyhq.com/linear/b7669c4b-eeca-421d-ba9a-d90203f6fcb2", "Product Manager")
    second_id = _insert("https://jobs.ashbyhq.com/linear/19", "Product Manager")

    first = dict(get_job_by_id(first_id))
    second = dict(get_job_by_id(second_id))

    assert job_main.mobile_command_alias_for_job(first) == "linear-product-manager-b7669c"
    assert job_main.mobile_command_alias_for_job(second) == "linear-product-manager-19"


def test_ambiguous_alias_returns_useful_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/linear/ambiguous-one", "Product Manager")
    _insert("https://jobs.ashbyhq.com/linear/ambiguous-two", "Product Manager")

    main(["telegram-command", "applied linear-product-manager"])

    result = json.loads(capsys.readouterr().out)
    assert result["success"] is False
    assert result["message"] == "Alias matched multiple jobs. Use the stable fallback command from Telegram."


def test_stable_job_key_still_resolves_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/b7669c4b-eeca-421d-ba9a-d90203f6fcb2", "Product Manager")

    main(["telegram-command", "applied ashby:linear:b7669c4b-eeca-421d-ba9a-d90203f6fcb2"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert row is not None and row["application_status"] == "applied"


def test_job_url_identifier_resolves_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    url = "https://jobs.ashbyhq.com/linear/url-command"
    job_id = _insert(url, "Product Manager")

    main(["telegram-command", f"save {url}"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert row is not None and row["application_status"] == "saved"


def test_parser_accepts_application_lifecycle_commands():
    cases = {
        "rejected 19 Not selected": ("rejected", "Not selected"),
        "/rejected 19": ("rejected", ""),
        "reject 19 No fit": ("rejected", "No fit"),
        "interviewing 19 Recruiter screen": ("interviewing", "Recruiter screen"),
        "interview 19": ("interviewing", ""),
        "offer 19 Verbal offer": ("offer", "Verbal offer"),
        "withdrawn 19 Accepted another role": ("withdrawn", "Accepted another role"),
        "withdraw 19": ("withdrawn", ""),
    }
    for text, (action, note) in cases.items():
        parsed = parse_telegram_command(text)
        assert parsed.action == action
        assert parsed.job_id == 19
        assert parsed.note == note


def test_parser_parses_blocked_with_reason():
    parsed = parse_telegram_command("blocked 19 Ashby 90-day application limit")
    assert parsed.as_dict() == {"action": "blocked", "job_identifier": "19", "note": "Ashby 90-day application limit", "job_id": 19}


def test_parser_requires_blocked_reason():
    try:
        parse_telegram_command("blocked 19")
    except ValueError as exc:
        assert "Blocked reason is required" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_parser_parses_block_company_with_reason():
    parsed = parse_telegram_command("block-company elevenlabs Ashby 90-day application limit, recruiter/manual review only")
    assert parsed.action == "block-company"
    assert parsed.job_identifier == "elevenlabs"
    assert parsed.note == "Ashby 90-day application limit, recruiter/manual review only"


def test_parser_parses_saved_alias():
    parsed = parse_telegram_command("saved ashby:linear:saved-alias")
    assert parsed.action == "save"
    assert parsed.job_identifier == "ashby:linear:saved-alias"


def test_telegram_command_block_company_creates_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["telegram-command", "block-company elevenlabs Ashby 90-day application limit, recruiter/manual review only"])

    result = json.loads(capsys.readouterr().out)
    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    assert result["success"] is True
    assert result["new_status"] == "blocked"
    assert result["company"] == "elevenlabs"
    assert result["message"] == "Blocked company: elevenlabs. Strategy: recruiter/manual review."
    assert store["elevenlabs"]["status"] == "blocked"
    assert store["elevenlabs"]["reason"] == "Ashby 90-day application limit, recruiter/manual review only"


def test_telegram_command_block_alias_updates_blocked_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/block-command", "Block PM")

    main(["telegram-command", f"block {job_id} Ashby 90-day application limit"])

    result = json.loads(capsys.readouterr().out)
    row = get_job_by_id(job_id)
    assert result["success"] is True
    assert result["new_status"] == "blocked"
    assert row is not None and row["application_status"] == "blocked"


def test_parser_parses_block_company_with_days_flag():
    parsed = parse_telegram_command("block-company elevenlabs --days 90 Ashby 90-day application limit, recruiter/manual review only")
    assert parsed.action == "block-company"
    assert parsed.job_identifier == "elevenlabs"
    assert parsed.days == 90
    assert parsed.note == "Ashby 90-day application limit, recruiter/manual review only"


def test_parser_parses_block_company_with_bare_days():
    parsed = parse_telegram_command("block-company elevenlabs 90 Ashby 90-day application limit, recruiter/manual review only")
    assert parsed.days == 90
    assert parsed.note == "Ashby 90-day application limit, recruiter/manual review only"


def test_telegram_command_supports_temporary_company_block(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()

    main(["telegram-command", "block-company elevenlabs --days 90 Ashby 90-day application limit, recruiter/manual review only"])

    result = json.loads(capsys.readouterr().out)
    store = json.loads((tmp_path / "data/company_application_blocks.json").read_text())
    assert result["success"] is True
    assert store["elevenlabs"]["status"] == "blocked"
    assert store["elevenlabs"]["expires_at"]

class _FakeTelegramResponse:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        return None
    def json(self):
        return self._payload


def _telegram_update(update_id: int, text: str, chat_id: str = "123") -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


def test_process_telegram_updates_parses_applied_update(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    job_id = _insert("https://jobs.ashbyhq.com/linear/process-applied", "Process Applied PM")
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [_telegram_update(101, "applied ashby:linear:process-applied")]}))

    result = job_main.process_telegram_updates()

    row = get_job_by_id(job_id)
    store = json.loads((tmp_path / "data/telegram_processed_updates.json").read_text())
    assert result["commands_processed"] == 1
    assert row is not None and row["application_status"] == "applied"
    assert store["last_update_id"] == 101
    assert store["processed_update_ids"] == [101]
    assert store["processed_updates"][0]["command"] == "applied ashby:linear:process-applied"
    assert sent == ["Marked applied: linear Process Applied PM."]


def test_process_telegram_updates_ignores_non_command_messages(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [_telegram_update(102, "thanks!")]}))

    result = job_main.process_telegram_updates()

    store = json.loads((tmp_path / "data/telegram_processed_updates.json").read_text())
    assert result["commands_processed"] == 0
    assert store["last_update_id"] == 102
    assert store["processed_update_ids"] == []
    assert sent == ["No new commands found"]


def test_process_telegram_updates_quiet_if_empty_sends_no_message(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": []}))

    result = job_main.process_telegram_updates(quiet_if_empty=True)

    assert result["commands_processed"] == 0
    assert sent == []
    assert "No new commands found" in capsys.readouterr().out


def test_process_telegram_updates_failed_command_sends_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [_telegram_update(103, "skip 999 Not relevant")]}))

    result = job_main.process_telegram_updates(quiet_if_empty=True)

    assert result["commands_processed"] == 1
    assert sent and sent[0].startswith("Command failed:")


def test_process_telegram_updates_ignores_already_processed_update_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/linear/already-processed", "Already Processed PM")
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "telegram_processed_updates.json").write_text(json.dumps({"last_update_id": 99, "processed_update_ids": [100], "processed_updates": []}) + "\n")
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [_telegram_update(100, "applied ashby:linear:already-processed")]}))

    result = job_main.process_telegram_updates()

    store = json.loads((tmp_path / "data/telegram_processed_updates.json").read_text())
    assert result["commands_processed"] == 0
    assert store["processed_update_ids"] == [100]
    assert sent == ["No new commands found"]


def test_process_telegram_updates_supports_stable_key_and_mobile_alias(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    stable_id = _insert("https://jobs.ashbyhq.com/linear/stable-process", "Stable Process PM")
    alias_id = _insert("https://jobs.ashbyhq.com/linear/mobile-process", "Mobile Process PM")
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [
        _telegram_update(201, "applied ashby:linear:stable-process"),
        _telegram_update(202, "save linear-mobile-process-pm"),
    ]}))

    result = job_main.process_telegram_updates()

    stable = get_job_by_id(stable_id)
    alias = get_job_by_id(alias_id)
    assert result["commands_processed"] == 2
    assert stable is not None and stable["application_status"] == "applied"
    assert alias is not None and alias["application_status"] == "saved"


def test_process_telegram_updates_no_duplicate_history_on_rerun(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert("https://jobs.ashbyhq.com/linear/no-duplicate", "No Duplicate PM")
    sent = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(job_main, "send_message_with_credentials", lambda text, bot_token, chat_id: sent.append(text))
    monkeypatch.setattr(job_main.requests, "get", lambda *args, **kwargs: _FakeTelegramResponse({"ok": True, "result": [_telegram_update(301, "applied ashby:linear:no-duplicate")]}))

    first = job_main.process_telegram_updates()
    second = job_main.process_telegram_updates()

    application_store = json.loads((tmp_path / "data/application_status.json").read_text())
    assert first["commands_processed"] == 1
    assert second["commands_processed"] == 0
    assert len(application_store["ashby:linear:no-duplicate"]["status_history"]) == 1
