"""Parser for safe Telegram job status commands."""

from __future__ import annotations

from dataclasses import dataclass
import re

DANGEROUS_SHELL_CHARS = re.compile(r"[;|`$<>\\\r\n]")
JOB_ID_RE = re.compile(r"^[0-9]+$")


@dataclass(frozen=True)
class TelegramStatusCommand:
    action: str
    job_identifier: str
    note: str = ""
    days: int | None = None
    expires_at: str | None = None

    @property
    def job_id(self) -> int | None:
        return int(self.job_identifier) if JOB_ID_RE.fullmatch(self.job_identifier) else None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"action": self.action, "job_identifier": self.job_identifier, "note": self.note}
        if self.job_id is not None:
            payload["job_id"] = self.job_id
        if self.days is not None:
            payload["days"] = self.days
        if self.expires_at is not None:
            payload["expires_at"] = self.expires_at
        return payload


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
    elif verb in {"save", "/save", "saved", "/saved"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "save"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"block-company", "/block-company"}:
        if len(parts) < 3:
            raise ValueError("Missing company or reason.")
        action = "block-company"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"blocked", "/blocked", "block", "/block"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "blocked"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"rejected", "/rejected", "reject", "/reject"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "rejected"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"interviewing", "/interviewing", "interview", "/interview"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "interviewing"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"offer", "/offer"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "offer"
        job_id_token = parts[1]
        note_parts = parts[2:]
    elif verb in {"withdrawn", "/withdrawn", "withdraw", "/withdraw"}:
        if len(parts) < 2:
            raise ValueError("Missing job id.")
        action = "withdrawn"
        job_id_token = parts[1]
        note_parts = parts[2:]
    else:
        raise ValueError("Unsupported command.")

    job_identifier = job_id_token.strip()
    if not job_identifier:
        raise ValueError("Job identifier is required.")
    if JOB_ID_RE.fullmatch(job_identifier) and int(job_identifier) <= 0:
        raise ValueError("Job id must be positive.")

    days: int | None = None
    expires_at: str | None = None
    if action == "block-company":
        if note_parts and note_parts[0] == "--days":
            if len(note_parts) < 3:
                raise ValueError("Missing company block days or reason.")
            try:
                days = int(note_parts[1])
            except ValueError as exc:
                raise ValueError("Company block days must be an integer.") from exc
            note_parts = note_parts[2:]
        elif note_parts and note_parts[0] == "--expires-at":
            if len(note_parts) < 3:
                raise ValueError("Missing company block expiration or reason.")
            expires_at = note_parts[1]
            note_parts = note_parts[2:]
        elif note_parts and JOB_ID_RE.fullmatch(note_parts[0]) and len(note_parts) >= 2:
            days = int(note_parts[0])
            note_parts = note_parts[1:]

    note = " ".join(note_parts).strip()
    if action in {"applied", "save"} and note:
        raise ValueError("Ambiguous command text.")
    if action == "skip" and not note:
        raise ValueError("Skip reason is required.")
    if action == "blocked" and not note:
        raise ValueError("Blocked reason is required.")
    if action == "block-company" and not note:
        raise ValueError("Company block reason is required.")

    return TelegramStatusCommand(action=action, job_identifier=job_identifier, note=note, days=days, expires_at=expires_at)
