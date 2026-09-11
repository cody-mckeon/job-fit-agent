from dataclasses import replace
from pathlib import Path

from job_fit_agent.main import _tailor_base_resume_with_strategy, main
from job_fit_agent.resume_strategy import classify_resume_strategy


def test_yum_ai_strategy_transformation_lane():
    strategy = classify_resume_strategy(
        "Manager of AI Strategy & Transformation",
        "Build enterprise AI literacy, AI Academy, adoption, change enablement, communities of practice, champion network, responsible AI, stakeholder communications, senior leader updates, and measurable productivity outcomes.",
    )
    assert strategy.lane == "ai_strategy_transformation"
    assert strategy.headline == "AI Transformation Leader | Enterprise AI Adoption | Workflow Automation"
    assert strategy.projects[0] == "AI Marketing Intelligence Platform"
    assert "Job Fit Agent" in strategy.excluded_projects


def test_caesars_data_platform_lane():
    strategy = classify_resume_strategy(
        "Senior Product Manager, Customer Data Platform",
        "Own the CDP and Single Customer View, identity resolution, profile stitching, event tracking, taxonomy, schemas, data governance, consent, segmentation and activation in Snowflake.",
    )
    assert strategy.lane == "data_platform_analytics_product"
    assert strategy.projects[0] == "Site Audit QA Agent"


def test_lvcva_ai_systems_integration_lane():
    strategy = classify_resume_strategy(
        "Director of AI Systems Integration",
        "Define the AI stack, integration architecture, technical standards, connectors, governance guardrails, deployed agents, feedback loops, vendor partners and AI roadmap.",
    )
    assert strategy.lane == "ai_systems_integration"
    assert strategy.projects[0] == "RWLV Priority Governor Agent"
    assert strategy.include_slip18 is True
    assert strategy.max_slip18_bullets == 4


def test_insight_global_technical_project_program_lane():
    strategy = classify_resume_strategy(
        "Sr Project Manager - eCommerce Transformation",
        "Own the integrated project plan, RAID, dependencies, QA, UAT, release readiness, hypercare, vendor coordination, executive status and delivery governance.",
    )
    assert strategy.lane == "technical_project_program_management"
    assert strategy.projects[0] == "Site Audit QA Agent"


def test_low_confidence_falls_back_with_warning_state():
    strategy = classify_resume_strategy("Business Lead", "General leadership responsibilities")
    assert strategy.lane == "product_management"
    assert strategy.low_confidence is True
    assert strategy.include_slip18 is False


def test_slip18_is_included_after_resorts_world_with_lane_bullet_limit():
    source_resume = Path(__file__).resolve().parents[1] / "profile" / "base_resume.md"
    strategy = classify_resume_strategy(
        "AI Solutions Architect",
        "Design agent workflows, evaluation plans, and implementation architecture.",
    )
    strategy = replace(strategy, max_slip18_bullets=2)

    resume = _tailor_base_resume_with_strategy(
        source_resume.read_text(encoding="utf-8"), strategy
    )
    slip18 = resume.split(
        "### Founder | AI Transformation & Workflow Automation", 1
    )[1].split("### Technical Business Analyst / Project Manager", 1)[0]

    assert resume.index("**Resorts World Las Vegas**") < resume.index("**Slip 18**")
    assert resume.index("**Slip 18**") < resume.index("**Lake Havasu City**")
    assert "Part-time / Independent" in slip18
    assert sum(line.startswith("- ") for line in slip18.splitlines()) == 2


def test_slip18_is_omitted_for_traditional_product_lane():
    source_resume = Path(__file__).resolve().parents[1] / "profile" / "base_resume.md"
    strategy = classify_resume_strategy(
        "Product Manager",
        "Own the product roadmap, backlog, discovery, and feature prioritization.",
    )

    resume = _tailor_base_resume_with_strategy(
        source_resume.read_text(encoding="utf-8"), strategy
    )

    assert strategy.lane == "product_management"
    assert strategy.include_slip18 is False
    assert "**Slip 18**" not in resume


def test_yum_generated_resume_consumes_strategy(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    profile = tmp_path / "profile"
    profile.mkdir()
    source_resume = Path(__file__).resolve().parents[1] / "profile" / "base_resume.md"
    profile.joinpath("base_resume.md").write_text(source_resume.read_text(encoding="utf-8"), encoding="utf-8")
    job = {
        "id": 501, "title": "Manager of AI Strategy & Transformation", "company": "Yum Brands",
        "source": "manual", "url": "https://example.com/yum", "score": 95, "classification": "high_fit",
        "viability_level": "apply_now", "location_raw": "Remote US", "location": "Remote US",
        "geographic_eligibility": "eligible", "reasons": "[]", "red_flags": "[]", "viability_reasons": "[]",
        "status": "new", "role_family": "ai_transformation",
        "notes": "Enterprise AI strategy, AI literacy, AI adoption, change enablement, communities of practice, AI champion programs, responsible AI, stakeholder communications, senior leader updates, workflow automation, and impact measurement.",
    }
    monkeypatch.setattr("job_fit_agent.main.initialize", lambda: None)
    monkeypatch.setattr("job_fit_agent.main.get_job_by_id", lambda job_id: job)
    monkeypatch.setattr("job_fit_agent.main.update_status", lambda job_id, status: None)

    main(["prep-application", "501"])

    app = tmp_path / "applications" / "yum_brands_manager_of_ai_strategy_transformation_501"
    strategy = app.joinpath("resume_strategy.md").read_text(encoding="utf-8")
    resume = app.joinpath("submit_resume.md").read_text(encoding="utf-8")
    assert "ai_strategy_transformation" in strategy
    assert "AI Transformation Leader | Enterprise AI Adoption | Workflow Automation" in resume
    assert "## AI Transformation Methods" in resume
    assert "AI Literacy" in resume and "AI Champion Programs" in resume and "Responsible AI" in resume
    assert resume.index("### AI Marketing Intelligence Platform") < resume.index("### AI Product Design Operating System")
    assert "### Job Fit Agent" not in resume
    assert "Product Systems Builder" not in resume
