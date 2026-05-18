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
    preferred_role_families: list[str] = Field(default_factory=list)
    disliked_role_families: list[str] = Field(default_factory=list)


class CompanyWatchlist(BaseModel):
    """Company board tokens grouped by source."""

    greenhouse: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)



def _env_flag_enabled(name: str) -> bool:
    """Return True when an environment flag is set to a recognized true value."""
    return os.getenv(name, "").strip().lower() in {"true", "1", "yes", "on"}


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
    discovery_queue_path: Path = Field(default_factory=lambda: Path("config/discovery_queue.yaml"))
    enable_lever: bool = Field(default_factory=lambda: _env_flag_enabled("JOB_FIT_ENABLE_LEVER"))
    notifications_path: Path = Field(default_factory=lambda: Path("config/notifications.yaml"))
    discovery_terms_path: Path = Field(default_factory=lambda: Path("config/discovery_terms.yaml"))
    discovered_companies_path: Path = Field(default_factory=lambda: Path("data/discovered_companies.yaml"))
    discovery_seed_companies_path: Path = Field(default_factory=lambda: Path("config/discovery_seed_companies.yaml"))


class DiscoveryTerms(BaseModel):
    terms: list[str] = Field(default_factory=list)


class DiscoveredCompany(BaseModel):
    company: str
    source_guess: str = "unknown"
    careers_url: str = ""
    reason_discovered: str = ""
    status: str = "new"


class DiscoveredCompanies(BaseModel):
    companies: list[DiscoveredCompany] = Field(default_factory=list)


class SeedCompany(BaseModel):
    company: str = ""
    source_guess: str = "unknown"
    careers_url: str = ""
    reason_discovered: str = ""


class SeedCompanies(BaseModel):
    companies: list[SeedCompany] = Field(default_factory=list)


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


def save_company_watchlist(watchlist: CompanyWatchlist, path: str | Path | None = None) -> None:
    """Persist company watchlist to YAML."""
    config = AppConfig()
    watchlist_path = Path(path) if path else config.company_watchlist_path
    watchlist_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = (
        f"ashby:\n{_serialize_list(watchlist.ashby)}"
        f"greenhouse:\n{_serialize_list(watchlist.greenhouse)}"
        f"lever:\n{_serialize_list(watchlist.lever)}"
    )
    watchlist_path.write_text(yaml_text, encoding="utf-8")


def load_discovery_queue(path: str | Path | None = None) -> CompanyWatchlist:
    """Load discovered company queue from YAML."""
    config = AppConfig()
    queue_path = Path(path) if path else config.discovery_queue_path
    if not queue_path.exists():
        return CompanyWatchlist()
    loaded = _parse_simple_yaml(queue_path.read_text(encoding="utf-8"))
    return CompanyWatchlist(**loaded)


def save_discovery_queue(queue: CompanyWatchlist, path: str | Path | None = None) -> None:
    """Persist discovered company queue to YAML."""
    config = AppConfig()
    queue_path = Path(path) if path else config.discovery_queue_path
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = (
        f"ashby:\n{_serialize_list(queue.ashby)}"
        f"greenhouse:\n{_serialize_list(queue.greenhouse)}"
        f"lever:\n{_serialize_list(queue.lever)}"
    )
    queue_path.write_text(yaml_text, encoding="utf-8")


def _serialize_list(items: list[str]) -> str:
    if not items:
        return "  []\n"
    return "".join(f"  - {item}\n" for item in items)


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


def load_discovery_terms(path: str | Path | None = None) -> DiscoveryTerms:
    config = AppConfig()
    terms_path = Path(path) if path else config.discovery_terms_path
    if not terms_path.exists():
        return DiscoveryTerms()
    loaded = yaml.safe_load(terms_path.read_text(encoding="utf-8")) or {}
    terms = loaded.get("terms", []) if isinstance(loaded, dict) else []
    return DiscoveryTerms(terms=[str(term) for term in terms])



def load_seed_companies(path: str | Path | None = None) -> SeedCompanies:
    config = AppConfig()
    seed_path = Path(path) if path else config.discovery_seed_companies_path
    if not seed_path.exists():
        return SeedCompanies()
    loaded = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    companies = loaded.get("companies", []) if isinstance(loaded, dict) else []
    return SeedCompanies(companies=[SeedCompany(**company) for company in companies])

def load_discovered_companies(path: str | Path | None = None) -> DiscoveredCompanies:
    config = AppConfig()
    discovered_path = Path(path) if path else config.discovered_companies_path
    if not discovered_path.exists():
        return DiscoveredCompanies()
    loaded = yaml.safe_load(discovered_path.read_text(encoding="utf-8")) or {}
    companies = loaded.get("companies", []) if isinstance(loaded, dict) else []
    return DiscoveredCompanies(companies=[DiscoveredCompany(**company) for company in companies])


def save_discovered_companies(companies: DiscoveredCompanies, path: str | Path | None = None) -> None:
    config = AppConfig()
    discovered_path = Path(path) if path else config.discovered_companies_path
    discovered_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"companies": [company.model_dump() for company in companies.companies]}
    discovered_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
