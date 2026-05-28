"""Parser for safe Telegram job status commands."""

from __future__ import annotations

from dataclasses import dataclass
import re

DANGEROUS_SHELL_CHARS = re.compile(r"[;&|`$<>\\\r\n]")
JOB_ID_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class TelegramStatusCommand:
    action: str
    job_id: int
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"action": self.action, "job_id": self.job_id, "note": self.note}


def parse_telegram_command(text: str) -> TelegramStatusCommand:
    """Parse a Telegram message into a safe job status command."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Command text is required.")
    if DANGEROUS_SHELL_CHARS.search(text):
        raise ValueError("Command contains unsupported shell-like characters.")

    normalized = " ".join(text.strip().split())
    parts = normalized.split(" ")
    if not parts:
        raise ValueError("Command text is required.")

    verb = parts[0].lower()
    action: str
    job_id_token: str
    note_parts: list[str]

    if verb == "mark":
        if len(parts) < 3:
            raise ValueError("Missing job id.")
        if parts[1].lower() != "applied":
            raise ValueError("Unsupported command.")
        action = "applied"
        job_id_token = parts[2]
        note_parts = parts[3:]
    elif verb in {"applied", "/applied"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "applied"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"skip", "/skip"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "skip"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"save", "/save"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "save"
        job_id_token = parts[1]
        note_parts = parts[2:]
    else:
        raise ValueError("Unsupported command.")

    if not JOB_ID_RE.fullmatch(job_id_token):
        raise ValueError("Job id must be numeric.")
    job_id = int(job_id_token)
    if job_id <= 0:
        raise ValueError("Job id must be positive.")

    note = " ".join(note_parts).strip()
    if action in {"applied", "save"} and note:
        raise ValueError("Ambiguous command text.")
    if action == "skip" and not note:
        raise ValueError("Skip reason is required.")

    return TelegramStatusCommand(action=action, job_id=job_id, note=note)
