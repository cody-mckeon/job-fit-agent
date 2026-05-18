"""Discovery provider interfaces and seed implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from job_fit_agent.config import DiscoveredCompany, SeedCompany, SeedCompanies

_ALLOWED_SOURCES = {"ashby", "greenhouse", "lever", "unknown"}


class DiscoveryProvider(ABC):
    """Provider abstraction for company discovery."""

    @abstractmethod
    def discover(self, terms: list[str]) -> list[DiscoveredCompany]:
        """Discover companies from search terms."""


class StaticCompanyProvider(DiscoveryProvider):
    """Manual provider backed by configured seed companies."""

    def __init__(self, seeds: SeedCompanies):
        self._seeds = seeds

    def discover(self, terms: list[str]) -> list[DiscoveredCompany]:
        _ = terms  # Terms are search intent only for this provider.
        companies: list[DiscoveredCompany] = []
        for seed in self._seeds.companies:
            if not _is_valid_seed(seed):
                continue
            companies.append(
                DiscoveredCompany(
                    company=seed.company.strip(),
                    source_guess=seed.source_guess.strip().lower(),
                    careers_url=seed.careers_url.strip(),
                    reason_discovered=seed.reason_discovered.strip(),
                    status="new",
                )
            )
        return companies


def _is_valid_seed(seed: SeedCompany) -> bool:
    if not seed.company.strip():
        print("Skipping invalid seed company: company must not be empty")
        return False
    if not seed.careers_url.strip():
        print(f"Skipping invalid seed company '{seed.company}': careers_url must not be empty")
        return False
    source = seed.source_guess.strip().lower()
    if source not in _ALLOWED_SOURCES:
        print(
            f"Skipping invalid seed company '{seed.company}': source_guess must be one of ashby, greenhouse, lever, unknown"
        )
        return False
    return True
