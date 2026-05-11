"""Configuration objects and loaders for job-fit-agent."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class TargetProfile(BaseModel):
    """Target role and location profile used by scoring."""

    target_titles: list[str] = Field(default_factory=list)
    target_keywords: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    acceptable_remote_terms: list[str] = Field(default_factory=list)
    local_terms: list[str] = Field(default_factory=list)
    non_remote_us_locations: list[str] = Field(default_factory=list)
    excluded_locations: list[str] = Field(default_factory=list)
    priority_companies: list[str] = Field(default_factory=list)
    industry_bias: list[str] = Field(default_factory=list)
    local_priority_companies: list[str] = Field(default_factory=list)


class CompanyWatchlist(BaseModel):
    """Company board tokens grouped by source."""

    greenhouse: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)




class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class NotificationConfig(BaseModel):
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

class AppConfig(BaseModel):
    """Runtime configuration for the application."""

    target_profile_path: Path = Field(default_factory=lambda: Path("config/target_profile.yaml"))
    company_watchlist_path: Path = Field(default_factory=lambda: Path("config/company_watchlist.yaml"))
    enable_lever: bool = False
    notifications_path: Path = Field(default_factory=lambda: Path("config/notifications.yaml"))


def _parse_simple_yaml(yaml_text: str) -> dict[str, list[str]]:
    """Parse simple key/list YAML used by config files."""
    data: dict[str, list[str]] = {}
    current_key: str | None = None

    for raw_line in yaml_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.endswith(":"):
            current_key = line[:-1]
            data[current_key] = []
            continue
        if line.startswith("-") and current_key:
            value = line[1:].strip()
            data[current_key].append(value)

    return data


def load_target_profile(path: str | Path | None = None) -> TargetProfile:
    """Load target profile from YAML."""
    config = AppConfig()
    profile_path = Path(path) if path else config.target_profile_path

    loaded = _parse_simple_yaml(profile_path.read_text(encoding="utf-8"))
    return TargetProfile(**loaded)


def load_company_watchlist(path: str | Path | None = None) -> CompanyWatchlist:
    """Load company watchlist from YAML."""
    config = AppConfig()
    watchlist_path = Path(path) if path else config.company_watchlist_path

    loaded = _parse_simple_yaml(watchlist_path.read_text(encoding="utf-8"))
    return CompanyWatchlist(**loaded)


def load_notification_config(path: str | Path | None = None) -> NotificationConfig:
    """Load notification settings from YAML with environment fallbacks."""
    config = AppConfig()
    notifications_path = Path(path) if path else config.notifications_path

    loaded = yaml.safe_load(notifications_path.read_text(encoding="utf-8")) or {}
    telegram_loaded = loaded.get("telegram", {}) if isinstance(loaded, dict) else {}
    notification_config = NotificationConfig(telegram=TelegramConfig(**telegram_loaded))

    if not notification_config.telegram.bot_token:
        notification_config.telegram.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not notification_config.telegram.chat_id:
        notification_config.telegram.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    return notification_config
