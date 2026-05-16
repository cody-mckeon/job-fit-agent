from job_fit_agent.config import (
    CompanyWatchlist,
    DiscoveredCompanies,
    DiscoveredCompany,
    DiscoveryTerms,
)
from job_fit_agent.main import approve_company, discover_companies, reject_company


def _patch_discovery_io(monkeypatch, discovered: DiscoveredCompanies, terms: DiscoveryTerms) -> dict:
    state = {"discovered": discovered, "watchlist": CompanyWatchlist()}

    monkeypatch.setattr("job_fit_agent.main.load_discovery_terms", lambda: terms)
    monkeypatch.setattr("job_fit_agent.main.load_discovered_companies", lambda: state["discovered"])
    monkeypatch.setattr("job_fit_agent.main.save_discovered_companies", lambda value: state.__setitem__("discovered", value))
    monkeypatch.setattr("job_fit_agent.main.load_company_watchlist", lambda: state["watchlist"])
    monkeypatch.setattr("job_fit_agent.main.save_company_watchlist", lambda value: state.__setitem__("watchlist", value))
    return state


def test_discovered_companies_are_saved(monkeypatch) -> None:
    state = _patch_discovery_io(monkeypatch, DiscoveredCompanies(), DiscoveryTerms(terms=["AI agents"]))

    discover_companies()

    assert len(state["discovered"].companies) == 1
    assert state["discovered"].companies[0].company == "ai-agents"


def test_duplicate_companies_not_duplicated(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(companies=[DiscoveredCompany(company="ai-agents")]),
        DiscoveryTerms(terms=["AI agents"]),
    )

    discover_companies()

    assert len(state["discovered"].companies) == 1


def test_approved_company_moves_to_watchlist(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(
            companies=[
                DiscoveredCompany(company="acme", source_guess="greenhouse", careers_url="https://boards.greenhouse.io/acme")
            ]
        ),
        DiscoveryTerms(),
    )

    approve_company("acme")

    assert state["watchlist"].greenhouse == ["acme"]
    assert state["discovered"].companies[0].status == "approved"


def test_rejected_company_is_ignored(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(companies=[DiscoveredCompany(company="acme", source_guess="ashby")]),
        DiscoveryTerms(),
    )

    reject_company("acme")

    assert state["discovered"].companies[0].status == "rejected"
    assert state["watchlist"].ashby == []


def test_unknown_source_stays_in_review(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(companies=[DiscoveredCompany(company="mystery", source_guess="unknown")]),
        DiscoveryTerms(),
    )

    approve_company("mystery")

    assert state["discovered"].companies[0].status == "approved"
    assert state["watchlist"].ashby == []
    assert state["watchlist"].greenhouse == []
    assert state["watchlist"].lever == []
