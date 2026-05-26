from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from job_fit_agent.config import load_notification_config

LOGGER = logging.getLogger(__name__)


def send_message_with_credentials(text: str, bot_token: str, chat_id: str) -> None:
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = Request(
        url=f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=10) as response:
            if response.status >= 400:
                LOGGER.warning("Telegram notification failed with HTTP status %s", response.status)
    except (HTTPError, URLError) as exc:  # pragma: no cover
        LOGGER.warning("Telegram notification failed: %s", exc)


def send_message(text: str) -> None:
    config = load_notification_config().telegram
    if not config.enabled:
        return
    if not config.bot_token or not config.chat_id:
        LOGGER.warning("Telegram notifications enabled but bot_token/chat_id not configured.")
        return

    send_message_with_credentials(text=text, bot_token=config.bot_token, chat_id=config.chat_id)


def send_document_with_credentials(file_path: str, caption: str, bot_token: str, chat_id: str) -> None:
    boundary = f"----jobfit{uuid4().hex}"
    data = bytearray()

    def _append_field(name: str, value: str) -> None:
        data.extend(f"--{boundary}\r\n".encode("utf-8"))
        data.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        data.extend(value.encode("utf-8"))
        data.extend(b"\r\n")

    _append_field("chat_id", chat_id)
    _append_field("caption", caption)

    path = Path(file_path)
    data.extend(f"--{boundary}\r\n".encode("utf-8"))
    data.extend(
        f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'.encode("utf-8")
    )
    data.extend(b"Content-Type: application/zip\r\n\r\n")
    data.extend(path.read_bytes())
    data.extend(b"\r\n")
    data.extend(f"--{boundary}--\r\n".encode("utf-8"))

    request = Request(
        url=f"https://api.telegram.org/bot{bot_token}/sendDocument",
        data=bytes(data),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )

    with urlopen(request, timeout=30) as response:
        if response.status >= 400:
            raise RuntimeError(f"Telegram document upload failed with HTTP status {response.status}")
