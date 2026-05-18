from job_fit_agent.config import (
    CompanyWatchlist,
    DiscoveredCompanies,
    DiscoveredCompany,
    DiscoveryTerms,
    SeedCompanies,
    SeedCompany,
)
from job_fit_agent.main import approve_company, discover_companies, reject_company


def _patch_discovery_io(
    monkeypatch,
    discovered: DiscoveredCompanies,
    terms: DiscoveryTerms,
    seeds: SeedCompanies | None = None,
) -> dict:
    state = {
        "discovered": discovered,
        "watchlist": CompanyWatchlist(),
        "seeds": seeds or SeedCompanies(),
    }

    monkeypatch.setattr("job_fit_agent.main.load_discovery_terms", lambda: terms)
    monkeypatch.setattr("job_fit_agent.main.load_seed_companies", lambda: state["seeds"])
    monkeypatch.setattr("job_fit_agent.main.load_discovered_companies", lambda: state["discovered"])
    monkeypatch.setattr("job_fit_agent.main.save_discovered_companies", lambda value: state.__setitem__("discovered", value))
    monkeypatch.setattr("job_fit_agent.main.load_company_watchlist", lambda: state["watchlist"])
    monkeypatch.setattr("job_fit_agent.main.save_company_watchlist", lambda value: state.__setitem__("watchlist", value))
    return state


def test_seed_companies_are_saved_as_discovered_companies(monkeypatch, capsys) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(),
        DiscoveryTerms(terms=["AI agents"]),
        SeedCompanies(
            companies=[
                SeedCompany(
                    company="hebbia",
                    source_guess="ashby",
                    careers_url="https://jobs.ashbyhq.com/hebbia",
                    reason_discovered="AI workflow automation company",
                )
            ]
        ),
    )

    discover_companies()
    output = capsys.readouterr().out

    assert len(state["discovered"].companies) == 1
    assert state["discovered"].companies[0].company == "hebbia"
    assert "Loaded 1 terms" in output
    assert "Discovered 1 companies" in output


def test_discovery_terms_are_not_saved_as_companies(monkeypatch) -> None:
    state = _patch_discovery_io(monkeypatch, DiscoveredCompanies(), DiscoveryTerms(terms=["AI agents"]))

    discover_companies()

    assert len(state["discovered"].companies) == 0


def test_duplicate_discovered_companies_are_skipped(monkeypatch, capsys) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(companies=[DiscoveredCompany(company="hebbia", source_guess="ashby")]),
        DiscoveryTerms(terms=["AI agents"]),
        SeedCompanies(
            companies=[
                SeedCompany(
                    company="hebbia",
                    source_guess="ashby",
                    careers_url="https://jobs.ashbyhq.com/hebbia",
                    reason_discovered="AI workflow automation company",
                )
            ]
        ),
    )

    discover_companies()
    output = capsys.readouterr().out

    assert len(state["discovered"].companies) == 1
    assert "Skipped 1 duplicates" in output


def test_invalid_seed_company_is_ignored(monkeypatch, capsys) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(),
        DiscoveryTerms(terms=[]),
        SeedCompanies(companies=[SeedCompany(company="badco", source_guess="workday", careers_url="", reason_discovered="x")]),
    )

    discover_companies()
    output = capsys.readouterr().out

    assert len(state["discovered"].companies) == 0
    assert "Skipping invalid seed company 'badco': careers_url must not be empty" in output


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


def test_approve_company_works_after_seed_discovery(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(),
        DiscoveryTerms(),
        SeedCompanies(
            companies=[
                SeedCompany(
                    company="hebbia",
                    source_guess="ashby",
                    careers_url="https://jobs.ashbyhq.com/hebbia",
                    reason_discovered="AI workflow automation company",
                )
            ]
        ),
    )

    discover_companies()
    approve_company("hebbia")

    assert state["watchlist"].ashby == ["hebbia"]
    assert state["discovered"].companies[0].status == "approved"


def test_approved_lever_company_moves_to_lever_watchlist(monkeypatch) -> None:
    state = _patch_discovery_io(
        monkeypatch,
        DiscoveredCompanies(
            companies=[
                DiscoveredCompany(company="ramp", source_guess="lever", careers_url="https://jobs.lever.co/ramp")
            ]
        ),
        DiscoveryTerms(),
    )

    approve_company("ramp")

    assert state["watchlist"].lever == ["ramp"]
    assert state["discovered"].companies[0].status == "approved"
