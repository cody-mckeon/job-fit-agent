from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from job_fit_agent.config import load_notification_config

LOGGER = logging.getLogger(__name__)


def send_message(text: str) -> None:
    config = load_notification_config().telegram
    if not config.enabled:
        return
    if not config.bot_token or not config.chat_id:
        LOGGER.warning("Telegram notifications enabled but bot_token/chat_id not configured.")
        return

    payload = json.dumps({"chat_id": config.chat_id, "text": text}).encode("utf-8")
    request = Request(
        url=f"https://api.telegram.org/bot{config.bot_token}/sendMessage",
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
