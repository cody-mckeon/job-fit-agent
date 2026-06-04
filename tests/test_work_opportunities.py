import json
from pathlib import Path

from job_fit_agent.main import block_company, main
from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_job_by_url, initialize, upsert_job
from job_fit_agent.scoring import score_job
from job_fit_agent.config import load_target_profile
from job_fit_agent.work_opportunities import add_rfp, add_work_opportunity, opportunity_review


def _insert_job(
    company: str,
    title: str,
    *,
    score: int = 45,
    classification: str = "near_fit",
    viability: str = "review",
    description: str = "Generic program management coordination.",
) -> int:
    slug = title.lower().replace(" ", "-")
    url = f"https://jobs.ashbyhq.com/{company.lower()}/{slug}"
    job = JobPosting(
        source="ashby",
        company=company,
        title=title,
        location="Remote US",
        location_raw="Remote US",
        geographic_eligibility="eligible",
        url=url,
        description=description,
    )
    fit = FitScore(
        total_score=score,
        classification=classification,
        role_family="program",
        viability_score=score,
        viability_level=viability,
        reasons=[description],
        red_flags=[],
    )
    upsert_job(job, fit)
    row = get_job_by_url(url)
    assert row is not None
    return int(row["id"])


def _record_from_output(output: str) -> dict:
    return json.loads(output)


def test_add_work_opportunity_creates_durable_record(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    main([
        "add-work-opportunity",
        "--title",
        "AI workflow automation pilot",
        "--company",
        "Local hospitality group",
        "--type",
        "local_business",
        "--source",
        "manual",
        "--source-detail",
        "Identified through local Las Vegas business research",
        "--priority",
        "high",
        "--status",
        "qualify",
        "--why-fit",
        "Potential agent deployment opportunity for operations, reporting, and workflow automation",
        "--next-action",
        "Research pain points and prepare diagnostic outreach",
    ])

    record = _record_from_output(capsys.readouterr().out)
    stored = json.loads(Path("data/work_opportunities.json").read_text())
    assert stored[0]["opportunity_id"] == record["opportunity_id"]
    assert stored[0]["opportunity_type"] == "local_business"
    assert stored[0]["status"] == "qualify"
    assert stored[0]["priority"] == "high"


def test_add_rfp_creates_rfp_opportunity(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    main([
        "add-rfp",
        "--title",
        "AI operations workflow RFP",
        "--organization",
        "County Innovation Office",
        "--deadline",
        "2099-06-15",
        "--source",
        "government",
        "--priority",
        "high",
        "--why-fit",
        "Agent deployment and workflow automation for internal operations",
    ])

    record = _record_from_output(capsys.readouterr().out)
    assert record["opportunity_type"] == "rfp"
    assert record["company"] == "County Innovation Office"
    assert record["status"] == "proposal_needed"


def test_work_opportunities_groups_by_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    add_work_opportunity(title="Pilot", company="A", opportunity_type="contract_1099", source="manual", status="pursue")
    add_work_opportunity(title="Proposal", company="B", opportunity_type="rfp", source="government", status="proposal_needed")
    add_work_opportunity(title="Blocked lead", company="C", opportunity_type="manual_lead", source="manual", status="blocked")
    add_work_opportunity(title="Submitted bid", company="D", opportunity_type="rfp", source="government", status="submitted")

    main(["work-opportunities"])

    output = capsys.readouterr().out
    assert "Pursue now" in output
    assert "Proposal needed" in output
    assert "Blocked" in output
    assert "Submitted / waiting" in output
    assert "title: Pilot" in output
    assert "title: Proposal" in output


def test_opportunity_review_can_recommend_non_w2_over_weak_w2_job(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("WeakCo", "Program Manager", score=42, classification="near_fit", viability="review")
    opp = add_work_opportunity(
        title="AI agent workflow automation pilot",
        company="Local Operations Group",
        opportunity_type="contract_1099",
        source="manual",
        status="pursue",
        priority="high",
        fit_score=82,
        revenue_potential="high",
        relationship_value="high",
        why_fit="AI agent deployment, workflow automation, product systems, and business process improvement",
        next_action="Send diagnostic pilot outreach today",
    )

    payload = opportunity_review()

    assert payload["recommended_opportunity_type"] == "contract_1099"
    assert payload["recommended_opportunity_id"] == opp["opportunity_id"]
    assert payload["recommended_company"] == "Local Operations Group"


def test_blocked_w2_company_does_not_prevent_1099_or_rfp_strategy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job(
        "BlockedCo",
        "AI Agents Product Lead",
        score=95,
        classification="high_fit",
        viability="apply_now",
        description="AI agents workflow automation product systems",
    )
    block_company("BlockedCo", "W2 application cooldown", expires_at="2099-12-31", quiet=True)
    opp = add_rfp(
        title="AI workflow automation RFP",
        organization="BlockedCo",
        deadline="2099-06-15",
        source="government",
        priority="high",
        why_fit="RFP is a separate vendor strategy for AI agents and workflow automation",
    )

    payload = opportunity_review()

    assert payload["recommended_opportunity_type"] == "rfp"
    assert payload["recommended_company"] == "BlockedCo"
    assert payload["recommended_opportunity_id"] == opp["opportunity_id"]


def test_prep_rfp_creates_proposal_prep_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    opp = add_rfp(title="Automation RFP", organization="City Office", deadline="2099-06-15")

    main(["prep-rfp", opp["opportunity_id"]])

    payload = _record_from_output(capsys.readouterr().out)
    folder = Path(payload["folder"])
    assert folder.exists()
    assert (folder / "opportunity_brief.md").exists()
    assert (folder / "proposed_solution_outline.md").exists()


def test_prep_1099_creates_contract_prep_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    opp = add_work_opportunity(title="Ops automation contract", company="Studio", opportunity_type="contract_1099", source="manual")

    main(["prep-1099", opp["opportunity_id"]])

    payload = _record_from_output(capsys.readouterr().out)
    folder = Path(payload["folder"])
    assert folder.exists()
    assert (folder / "qualification_checklist.md").exists()
    assert (folder / "next_steps.md").exists()


def test_prep_local_outreach_creates_outreach_prep_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    opp = add_work_opportunity(title="Local AI reporting lead", company="Restaurant Group", opportunity_type="local_business", source="local")

    main(["prep-local-outreach", opp["opportunity_id"]])

    payload = _record_from_output(capsys.readouterr().out)
    folder = Path(payload["folder"])
    assert folder.exists()
    assert (folder / "outreach_note.md").exists()
    assert (folder / "risks.md").exists()


def test_existing_job_scoring_remains_unchanged():
    job = JobPosting(
        source="ashby",
        company="linear",
        title="AI Product Operations Manager",
        location="Remote US",
        description="Deploy AI agents, workflow automation, product analytics, and internal AI operations.",
        url="https://jobs.ashbyhq.com/linear/ai-product-ops",
    )

    fit = score_job(job, load_target_profile())

    assert fit.total_score > 0
    assert fit.classification in {"high_fit", "near_fit", "low_fit"}
