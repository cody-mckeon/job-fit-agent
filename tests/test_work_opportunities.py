import json
from datetime import UTC, datetime, timedelta
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



def test_prep_rfp_creates_decision_grade_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    opp = add_rfp(
        title="AI workflow automation RFP",
        organization="County Innovation Office",
        url="https://example.com/rfp",
        deadline="2099-06-15",
        source="government",
        source_detail="County procurement portal",
        priority="high",
        why_fit="Scope mentions workflow automation, AI operations, and internal process improvement.",
        notes="Need to verify portal registration, data access, integrations, and insurance.",
        next_action="Download RFP package and verify required forms.",
    )

    main(["prep-rfp", opp["opportunity_id"]])

    payload = _record_from_output(capsys.readouterr().out)
    folder = Path(payload["folder"])
    risks = (folder / "risks.md").read_text()
    go_no_go = (folder / "go_no_go_checklist.md").read_text()
    required_documents = (folder / "required_documents.md").read_text()
    questions = (folder / "questions_to_ask.md").read_text()
    summary = (folder / "rfp_summary.md").read_text()
    next_steps = (folder / "next_steps.md").read_text()

    assert "Add delivery, procurement, relationship" not in risks
    assert "Deadline risk" in risks
    assert "Data access/security risk" in risks
    assert "## Preliminary recommendation:" in go_no_go
    assert "### Scope Fit" in go_no_go
    assert "Business entity information — Status: Unknown" in required_documents
    assert "portal registration — Status: Unknown" in required_documents
    assert "What data can be accessed?" in questions
    assert "licenses, insurance, certifications" in questions
    assert "## Current scores" in summary
    assert "## Immediate next action" in summary
    assert "Download RFP package and verify required forms." in summary
    assert "1. Open/review RFP URL or source package" in next_steps
    assert "7. If pursue, create proposal draft" in next_steps

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


def test_discovery_commands_exist_for_all_swim_lanes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    for command in [
        "discover-w2",
        "discover-contracts",
        "discover-rfps",
        "discover-local-businesses",
        "discover-relationships",
    ]:
        main([command, "--query", "AI workflow automation", "--location", "Las Vegas", "--limit", "1"])
        payload = _record_from_output(capsys.readouterr().out)
        assert payload["count"] == 1


def test_source_file_import_creates_normalized_opportunities_without_duplicates(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "contracts.json"
    source.write_text(json.dumps([
        {
            "title": "Intake workflow automation pilot",
            "company": "Clinic Ops Group",
            "url": "https://example.com/opportunities/intake-pilot",
            "description": "Clear business problem: manual intake workflow needs automation. Buyer email is published.",
            "contact": "ops@example.com",
        }
    ]))

    main(["discover-contracts", "--source-file", str(source), "--limit", "10"])
    first = _record_from_output(capsys.readouterr().out)
    main(["discover-contracts", "--source-file", str(source), "--limit", "10"])
    second = _record_from_output(capsys.readouterr().out)

    stored = json.loads(Path("data/work_opportunities.json").read_text())
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 1
    assert len(stored) == 1
    record = stored[0]
    assert record["opportunity_type"] == "contract_1099"
    assert record["fit_score"] > 0
    assert record["actionability_score"] > 0
    assert record["urgency_score"] >= 0
    assert record["recommended_next_action"]
    assert record["qualification"]["buyer_reachable"] is True


def test_rfp_with_close_deadline_gets_urgency(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    deadline = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    source = tmp_path / "rfps.json"
    source.write_text(json.dumps([
        {
            "title": "AI operations workflow RFP",
            "organization": "County Innovation Office",
            "deadline": deadline,
            "description": "Scope includes workflow automation and internal AI operations.",
            "required_documents": ["proposal", "pricing"],
        }
    ]))

    main(["discover-rfps", "--source-file", str(source)])
    payload = _record_from_output(capsys.readouterr().out)
    record = payload["opportunities"][0]

    assert record["opportunity_type"] == "rfp"
    assert record["urgency_score"] >= 90
    assert record["priority"] == "high"
    assert record["qualification"]["go_no_go"] == "go"


def test_discovered_strong_local_contract_or_rfp_beats_weak_w2_job(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("WeakCo", "Program Manager", score=38, classification="near_fit", viability="review")
    source = tmp_path / "local.json"
    source.write_text(json.dumps([
        {
            "title": "Restaurant back-office workflow automation pilot",
            "business": "Downtown Hospitality Group",
            "location": "Las Vegas",
            "description": "Manual spreadsheet reporting and scheduling workflow pain. Owner reachable for a simple pilot.",
            "owner": "Founder",
            "fit_score": 88,
            "actionability_score": 90,
            "revenue_potential": "high",
            "relationship_value": "high",
        }
    ]))
    main(["discover-local-businesses", "--source-file", str(source), "--location", "Las Vegas"])
    capsys.readouterr()

    payload = opportunity_review()

    assert payload["recommended_opportunity_type"] == "local_business"
    assert payload["recommended_company"] == "Downtown Hospitality Group"


def test_blocked_w2_company_can_still_appear_as_relationship_strategy_from_discovery(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    initialize()
    block_company("BlockedCo", "W2 application cooldown", expires_at="2099-12-31", quiet=True)
    source = tmp_path / "w2.json"
    source.write_text(json.dumps([
        {
            "title": "AI Agents Product Lead",
            "company": "BlockedCo",
            "url": "https://example.com/blockedco/ai-agents-product-lead",
            "description": "AI agents and workflow automation role with a reachable hiring manager.",
        }
    ]))

    main(["discover-w2", "--source-file", str(source)])
    payload = _record_from_output(capsys.readouterr().out)
    record = payload["opportunities"][0]

    assert record["opportunity_type"] == "relationship"
    assert record["status"] == "relationship_strategy"
    assert record["qualification"]["converted_from_blocked_w2"] is True
    assert "do not submit a W2 application" in record["recommended_next_action"]


def test_lane_default_status_and_next_actions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    contract = add_work_opportunity(title="Ops automation", company="Studio", opportunity_type="contract_1099", source="manual")
    local = add_work_opportunity(title="Back office pilot", company="Cafe", opportunity_type="local_business", source="local")
    relationship = add_work_opportunity(title="AI ops intro", company="Former colleague", opportunity_type="relationship", source="referral")

    assert contract["status"] == "qualify"
    assert contract["next_action"] == "qualify buyer, scope, budget, timeline, and delivery risk"
    assert local["status"] == "qualify"
    assert local["next_action"] == "research pain points and prepare diagnostic outreach"
    assert relationship["status"] == "relationship_strategy"
    assert relationship["next_action"] == "draft outreach and define reason to connect"


def test_csv_import_for_contract_rfp_and_local_business(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    for command, expected_type in [
        ("discover-contracts", "contract_1099"),
        ("discover-rfps", "rfp"),
        ("discover-local-businesses", "local_business"),
    ]:
        source = tmp_path / f"{command}.csv"
        source.write_text(f"title,company,description,url,deadline\nAI workflow pilot,Ops Group,workflow automation buyer reachable,https://example.com/{command}/pilot,2099-06-15\n")
        main([command, "--source-file", str(source)])
        payload = _record_from_output(capsys.readouterr().out)
        assert payload["created"] == 1
        assert payload["opportunities"][0]["opportunity_type"] == expected_type


def test_markdown_source_file_import(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "leads.md"
    source.write_text("- Clinic Ops Group - Intake workflow automation pilot - Manual intake pain https://example.com/intake\n")

    main(["discover-contracts", "--source-file", str(source)])
    payload = _record_from_output(capsys.readouterr().out)

    assert payload["created"] == 1
    assert payload["opportunities"][0]["company"] == "Clinic Ops Group"
    assert payload["opportunities"][0]["url"] == "https://example.com/intake"


def test_prep_commands_create_lane_specific_expected_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rfp = add_rfp(title="Automation RFP", organization="City Office", deadline="2099-06-15")
    contract = add_work_opportunity(title="Ops automation contract", company="Studio", opportunity_type="contract_1099", source="manual")
    local = add_work_opportunity(title="Local AI reporting lead", company="Restaurant Group", opportunity_type="local_business", source="local")

    expectations = [
        ("prep-rfp", rfp["opportunity_id"], {"rfp_summary.md", "go_no_go_checklist.md", "required_documents.md", "proposal_outline.md", "questions_to_ask.md", "risks.md", "next_steps.md"}),
        ("prep-1099", contract["opportunity_id"], {"opportunity_brief.md", "qualification_checklist.md", "scope_hypothesis.md", "pricing_hypothesis.md", "outreach_note.md", "risks.md", "next_steps.md"}),
        ("prep-local-outreach", local["opportunity_id"], {"business_profile.md", "pain_hypothesis.md", "ai_pilot_idea.md", "diagnostic_offer.md", "outreach_note.md", "follow_up_sequence.md"}),
    ]
    for command, opportunity_id, expected_files in expectations:
        main([command, opportunity_id])
        payload = _record_from_output(capsys.readouterr().out)
        folder = Path(payload["folder"])
        assert expected_files.issubset({path.name for path in folder.iterdir()})


def test_local_hospitality_ai_workflow_pilot_scores_after_add(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    record = add_work_opportunity(
        title="AI workflow automation pilot",
        company="Local hospitality group",
        opportunity_type="local_business",
        source="local",
        source_detail="Las Vegas business opportunity",
        priority="high",
        next_action="Research pain points and prepare diagnostic outreach",
        why_fit="Potential AI agent deployment for reporting, vendor coordination, and intake workflows",
        qualification={
            "likely_workflow_pain": True,
            "simple_pilot_opportunity": True,
        },
    )

    assert record["fit_score"] > 0
    assert record["actionability_score"] > 0
    assert record["urgency_score"] == 0


def test_rescore_work_opportunities_updates_existing_stale_zero_records(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()
    old_updated_at = "2024-01-01T00:00:00+00:00"
    Path("data/work_opportunities.json").write_text(json.dumps([
        {
            "opportunity_id": "work-stale-local",
            "dedupe_key": "opp:stale-local",
            "title": "AI workflow automation pilot",
            "company": "Local hospitality group",
            "opportunity_type": "local_business",
            "source": "local",
            "source_detail": "Las Vegas business opportunity",
            "priority": "high",
            "status": "qualify",
            "next_action": "Research pain points and prepare diagnostic outreach",
            "recommended_next_action": "Research pain points and prepare diagnostic outreach",
            "why_fit": "Potential AI agent deployment for reporting, vendor coordination, and intake workflows",
            "qualification": {"likely_workflow_pain": True, "simple_pilot_opportunity": True},
            "fit_score": 0,
            "actionability_score": 0,
            "urgency_score": 0,
            "revenue_potential": "unknown",
            "relationship_value": "medium",
            "created_at": old_updated_at,
            "updated_at": old_updated_at,
        }
    ]))

    main(["rescore-work-opportunities"])

    payload = _record_from_output(capsys.readouterr().out)
    stored = json.loads(Path("data/work_opportunities.json").read_text())
    assert payload["rescored"] == 1
    assert stored[0]["fit_score"] > 0
    assert stored[0]["actionability_score"] > 0
    assert stored[0]["updated_at"] != old_updated_at


def test_opportunity_review_uses_rescored_zero_value_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    initialize()
    _insert_job("WeakCo", "Program Manager", score=38, classification="near_fit", viability="review")
    Path("data").mkdir(exist_ok=True)
    Path("data/work_opportunities.json").write_text(json.dumps([
        {
            "opportunity_id": "work-stale-local",
            "dedupe_key": "opp:stale-local",
            "title": "AI workflow automation pilot",
            "company": "Local hospitality group",
            "opportunity_type": "local_business",
            "source": "local",
            "source_detail": "Las Vegas business opportunity",
            "priority": "high",
            "status": "qualify",
            "next_action": "Research pain points and prepare diagnostic outreach",
            "why_fit": "Potential AI agent deployment for reporting, vendor coordination, and intake workflows",
            "qualification": {"likely_workflow_pain": True, "simple_pilot_opportunity": True},
            "fit_score": 0,
            "actionability_score": 0,
            "urgency_score": 0,
        }
    ]))

    payload = opportunity_review()

    assert payload["recommended_opportunity_type"] == "local_business"
    assert payload["recommended_opportunity_id"] == "work-stale-local"


def test_rfp_example_still_scores_positively_without_deadline_urgency(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    record = add_rfp(
        title="AI workflow automation RFP",
        organization="County Innovation Office",
        source="government",
        why_fit="RFP scope includes AI agent deployment and workflow automation for internal operations.",
    )

    assert record["fit_score"] > 0
    assert record["actionability_score"] > 0
    assert record["urgency_score"] == 0
