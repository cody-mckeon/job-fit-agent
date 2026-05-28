from job_fit_agent.config import load_target_profile
from job_fit_agent.models import JobPosting
from job_fit_agent.scoring import score_job

TARGET_PROFILE = load_target_profile()


def _job(title: str, location: str = "Remote", description: str = "", company: str = "Test Co") -> JobPosting:
    return JobPosting(
        source="test",
        company=company,
        title=title,
        location=location,
        url="https://example.com/job",
        description=description,
    )


def test_pittsburgh_product_manager_gets_location_red_flag_and_stays_below_threshold() -> None:
    job = _job(
        title="Product Manager",
        location="Pittsburgh",
        description="Product roadmap for analytics platform.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert any("outside Las Vegas/Nevada" in flag for flag in fit.red_flags)
    assert fit.total_score < 45


def test_new_york_product_manager_gets_location_red_flag_and_stays_below_threshold() -> None:
    job = _job(
        title="Product Manager",
        location="Onsite - New York only",
        description="Own product delivery.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert any("outside Las Vegas/Nevada" in flag for flag in fit.red_flags)
    assert fit.total_score < 45


def test_remote_us_product_manager_scores_above_threshold() -> None:
    job = _job(
        title="Product Manager",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert fit.total_score >= 45
    assert fit.classification == "high_fit"


def test_las_vegas_product_manager_scores_above_threshold() -> None:
    job = _job(
        title="Senior Product Manager",
        location="Las Vegas, NV",
        description="Drive analytics and data roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert fit.total_score >= 45


def test_london_product_manager_gets_international_location_red_flag() -> None:
    job = _job(
        title="Senior Product Manager",
        location="London",
        description="Drive analytics and experimentation",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert any("International location" in flag for flag in fit.red_flags)


def test_remote_product_marketing_manager_is_near_fit() -> None:
    job = _job(
        title="Product Marketing Manager",
        location="Remote US",
        description="Lead GTM and messaging strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"
    assert "Product marketing role lacks product systems overlap" in fit.red_flags


def test_remote_technical_product_manager_is_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Remote US",
        description="Drive AI experimentation and analytics roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_remote_product_manager_is_actionable_high_fit() -> None:
    fit = score_job(_job("Product Manager", "Remote US", "Own product systems and experimentation roadmap."), TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_remote_product_engineer_is_actionable() -> None:
    fit = score_job(_job("Product Engineer", "Remote US", "Build internal tools and AI workflows."), TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}


def test_remote_engineering_manager_is_low_fit() -> None:
    job = _job(
        title="Engineering Manager",
        location="Remote US",
        description="Manage software delivery and people operations.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_blank_location_maps_to_review_geographic_eligibility() -> None:
    job = _job(
        title="Product Manager",
        location="",
        description="Own analytics roadmap for product platform.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"


def test_blank_location_does_not_receive_local_points() -> None:
    job = _job(
        title="Product Manager",
        location="",
        description="Work with Las Vegas stakeholders and data teams.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert all("Las Vegas/Henderson/Nevada" not in reason for reason in fit.reasons)


def test_remote_software_engineer_is_low_fit() -> None:
    job = _job(
        title="Software Engineer",
        location="Remote US",
        description="Build AI data platform for product teams.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_remote_product_engineer_is_near_or_high_fit() -> None:
    job = _job(
        title="Product Engineer",
        location="Remote US",
        description="Build AI product experiences and experimentation systems.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}


def test_remote_member_of_technical_staff_is_low_fit() -> None:
    job = _job(
        title="Member of Technical Staff",
        location="Remote US",
        description="Develop AI systems and product analytics capabilities.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_remote_technical_account_manager_is_near_fit() -> None:
    job = _job(
        title="Technical Account Manager",
        location="Remote US",
        description="Partner with enterprise customers for onboarding.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"


def test_forward_deployed_engineer_ai_ranks_high() -> None:
    job = _job(
        title="Forward Deployed Engineer, AI",
        location="Remote US",
        description="Build applied AI agents for enterprise deployment, integrations, and customer workflows.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"
    assert any("Forward deployed engineering role aligns" in reason for reason in fit.reasons)


def test_forward_deployed_product_engineer_ranks_high() -> None:
    job = _job(
        title="Forward Deployed Product Engineer",
        location="Remote US",
        description="Own customer-facing engineering and product implementation for AI workflow automation.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_forward_deployed_software_engineer_agents_ranks_high() -> None:
    job = _job(
        title="Forward Deployed Software Engineer, Agents",
        location="Remote US",
        description="Deliver agentic workflows, technical discovery, prototypes, and POC integrations.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_forward_deployed_engineer_without_context_is_not_auto_apply_now() -> None:
    job = _job(
        title="Forward Deployed Engineer",
        location="Remote US",
        description="General client delivery and stakeholder communication.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "low_fit"}
    assert fit.viability_level != "apply_now"


def test_technical_account_manager_stays_downranked() -> None:
    job = _job(
        title="Technical Account Manager",
        location="Remote US",
        description="Manage renewals and customer support escalation.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"


def test_solutions_consultant_stays_downranked_without_context() -> None:
    job = _job(
        title="Solutions Consultant",
        location="Remote US",
        description="Support pre-sales demos and customer presentations.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "low_fit"}


def test_field_service_engineer_stays_low_fit() -> None:
    job = _job(
        title="Field Service Engineer",
        location="Remote US",
        description="Install and maintain hardware systems onsite at customer facilities.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_onsite_product_manager_outside_nevada_not_high_fit() -> None:
    job = _job(
        title="Product Manager",
        location="Onsite - New York",
        description="Own product strategy and roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"low_fit", "near_fit"}
    assert fit.classification != "high_fit"
    assert any("outside Las Vegas/Nevada" in flag for flag in fit.red_flags)


def test_location_scoring_ignores_department_team_text() -> None:
    job = _job(
        title="Product Manager",
        location="",
        description="Own analytics roadmap.",
    )
    job.department = "New York"
    job.team = "London"

    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"
    assert all("International location" not in flag for flag in fit.red_flags)


def test_remote_workplace_type_contributes_to_fit() -> None:
    job = _job(
        title="Product Manager",
        location="United States",
        description="Drive AI experimentation and analytics roadmap.",
    )
    job.workplace_type = "Remote"

    fit = score_job(job, TARGET_PROFILE)
    assert any("Remote US" in reason for reason in fit.reasons)


def test_remote_unspecified_geography_stays_eligible() -> None:
    job = _job(
        title="Technical Product Manager",
        location="",
        description="Drive AI experimentation and analytics roadmap.",
    )
    job.workplace_type = "Remote"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"
    assert "Remote role with unspecified geography" in fit.red_flags


def test_hybrid_unspecified_geography_is_near_fit() -> None:
    job = _job(
        title="Product Marketing Manager",
        location="",
        description="Lead GTM messaging and partner closely with product.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"
    assert "Hybrid role with unspecified location" in fit.red_flags


def test_remote_us_is_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Remote United States",
        description="Own AI platform experimentation and analytics strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_singapore_product_manager_gets_international_location_red_flag() -> None:
    job = _job(
        title="Senior Product Manager",
        location="Singapore",
        description="Drive analytics and experimentation",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert any("International location" in flag for flag in fit.red_flags)


def test_priority_company_receives_boost() -> None:
    base_job = _job(
        title="Product Manager",
        company="Acme",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )
    priority_job = _job(
        title="Product Manager",
        company="Cursor",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )

    base_fit = score_job(base_job, TARGET_PROFILE)
    priority_fit = score_job(priority_job, TARGET_PROFILE)

    assert priority_fit.total_score == base_fit.total_score + 10
    assert "Priority company match (+10)" in priority_fit.reasons


def test_non_priority_company_does_not_receive_boost() -> None:
    job = _job(
        title="Product Manager",
        company="Acme",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert "Priority company match (+10)" not in fit.reasons


def test_data_scientist_is_data_science_low_fit() -> None:
    job = _job(
        title="Data Scientist",
        location="Remote US",
        description="Build predictive models for growth analytics.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "data_science"
    assert fit.classification == "low_fit"


def test_product_manager_is_product_management_high_fit() -> None:
    job = _job(
        title="Product Manager",
        location="Remote US",
        description="Own AI platform experimentation and analytics roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "product_management"
    assert fit.classification == "high_fit"


def test_technical_program_manager_is_product_operations_high_or_near_fit() -> None:
    job = _job(
        title="Technical Program Manager",
        location="Remote US",
        description="Lead cross-functional delivery for product initiatives.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "product_operations"
    assert fit.classification in {"high_fit", "near_fit"}


def test_product_marketing_manager_is_marketing_near_fit() -> None:
    job = _job(
        title="Product Marketing Manager",
        location="Remote US",
        description="Lead messaging and go-to-market strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "marketing"
    assert fit.classification == "near_fit"


def test_user_researcher_is_research_near_fit() -> None:
    job = _job(
        title="User Researcher",
        location="Remote US",
        description="Drive user interviews and research synthesis.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "research"
    assert fit.classification == "near_fit"


def test_chief_of_staff_is_executive_near_fit() -> None:
    job = _job(
        title="Chief of Staff",
        location="Remote US",
        description="Support strategic planning and executive operations.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.role_family == "executive"
    assert fit.classification == "near_fit"


def test_cursor_onsite_pm_downgrades_to_near_fit() -> None:
    job = _job(
        title="Product Manager",
        company="Cursor",
        location="Onsite - San Francisco, CA",
        description="Own AI platform experimentation and analytics roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"


def test_figma_sf_ny_pm_downgrades_to_near_fit() -> None:
    job = _job(
        title="Product Manager",
        company="Figma",
        location="Onsite - SF or NY",
        description="Lead AI product strategy and analytics experimentation.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "near_fit"


def test_linear_remote_pm_remains_high_fit() -> None:
    job = _job(
        title="Product Manager",
        company="Linear",
        location="Remote US",
        description="Own AI platform experimentation and analytics roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_replit_hybrid_unspecified_pm_is_not_low_fit() -> None:
    job = _job(
        title="Product Manager",
        company="Replit",
        location="",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}


def test_hospitality_product_manager_receives_industry_bias_boost() -> None:
    job = _job(
        title="Product Manager",
        location="Remote US",
        description="Own hospitality digital experience and experimentation roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert "Industry bias match: hospitality (+5)" in fit.reasons


def test_gaming_product_manager_receives_industry_bias_boost() -> None:
    job = _job(
        title="Product Manager, Gaming Platform",
        location="Remote US",
        description="Drive gaming loyalty and personalization strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert "Industry bias match: gaming (+5)" in fit.reasons


def test_martech_product_analytics_role_receives_industry_bias_boost() -> None:
    job = _job(
        title="Product Analytics Manager",
        location="Remote US",
        description="Lead martech analytics and experimentation instrumentation.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert "Industry bias match: martech (+5)" in fit.reasons


def test_casino_sales_role_does_not_become_high_fit() -> None:
    job = _job(
        title="Casino Sales Manager",
        location="Las Vegas, NV",
        description="Own casino enterprise sales strategy.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification != "high_fit"


def test_hospitality_software_engineer_remains_low_fit() -> None:
    job = _job(
        title="Hospitality Software Engineer",
        location="Remote US",
        description="Build hotel booking platform APIs.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_onsite_non_local_gaming_pm_does_not_become_high_fit() -> None:
    job = _job(
        title="Gaming Product Manager",
        location="Onsite - New York, NY",
        description="Own casino payments and loyalty product roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification != "high_fit"


def test_local_priority_company_receives_boost() -> None:
    base_job = _job(
        title="Product Manager",
        company="Acme",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )
    local_priority_job = _job(
        title="Product Manager",
        company="MGM Resorts",
        location="Remote US",
        description="Lead AI analytics experimentation strategy.",
    )

    base_fit = score_job(base_job, TARGET_PROFILE)
    local_priority_fit = score_job(local_priority_job, TARGET_PROFILE)

    assert local_priority_fit.total_score >= base_fit.total_score + 12
    assert "Local priority company match (+12)" in local_priority_fit.reasons
def test_foster_city_hybrid_role_downgrades_from_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Foster City, CA (Hybrid) In office M,W,F",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification != "high_fit"
    assert "Hybrid in-office requirement outside Las Vegas/Nevada" in fit.red_flags


def test_sf_ny_hybrid_role_downgrades_from_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="San Francisco, CA or New York, NY (Hybrid)",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification != "high_fit"
    assert "Hybrid in-office requirement outside Las Vegas/Nevada" in fit.red_flags


def test_remote_us_hybrid_role_can_still_be_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Remote US",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_las_vegas_hybrid_role_can_be_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Las Vegas, NV (Hybrid)",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"

def test_foster_city_hybrid_product_manager_is_not_high_fit() -> None:
    job = _job(
        title="Product Manager",
        location="Foster City, CA (Hybrid) In office M,W,F",
        description="Own AI platform experimentation and analytics roadmap.",
    )
    job.workplace_type = "Hybrid"

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification != "high_fit"
    assert "Hybrid in-office requirement outside Las Vegas/Nevada" in fit.red_flags


def test_product_engineer_with_10_years_not_apply_now() -> None:
    job = _job("Product Engineer", location="Remote USA", description="Requires 10+ years of experience in senior capacity.")
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level != "apply_now"


def test_staff_product_manager_with_10_years_is_stretch_or_skip() -> None:
    job = _job("Staff Product Manager", location="Remote USA", description="at least 10 years of experience and company-level product decisions")
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level in {"stretch", "skip"}


def test_remote_product_manager_4_6_years_is_apply_now_or_review() -> None:
    job = _job("Product Manager", location="Remote USA", description="4-6 years of experience with analytics")
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level in {"apply_now", "review"}


def test_stripe_staff_product_manager_with_10_years_not_apply_now() -> None:
    job = _job("Staff Product Manager", location="Remote USA", description="10+ years of experience in senior capacity and company-level product decisions", company="Stripe")
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level != "apply_now"


def test_foster_city_hybrid_viability_is_stretch_or_skip() -> None:
    job = _job(
        "Technical Product Manager",
        location="Foster City, CA",
        description="Own AI analytics roadmap.",
    )
    job.workplace_type = "Hybrid"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level in {"stretch", "skip"}
    assert "Hybrid role outside Las Vegas/Nevada" in fit.viability_reasons


def test_mexico_argentina_peru_remote_viability_is_skip() -> None:
    job = _job(
        "Product Manager",
        location="Remote - Mexico; Argentina; Peru",
        description="Own product roadmap.",
    )
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level == "skip"
    assert "Remote role limited to non-US geography" in fit.viability_reasons


def test_remote_usa_viability_is_apply_now() -> None:
    job = _job("Product Manager", location="USA", description="Own product roadmap.")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level == "apply_now"


def test_ai_product_engineer_role_scores_positively() -> None:
    job = _job(
        "AI Product Engineer",
        location="Remote US",
        description="Build agentic workflows, orchestration, internal tools, and rapid prototyping systems.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}
    assert fit.total_score >= 45


def test_workflow_automation_role_scores_positively() -> None:
    job = _job(
        "Workflow Automation Lead",
        location="Remote US",
        description="Own automation, integrations, APIs, and workflow systems for operations.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}


def test_agentic_systems_role_scores_positively() -> None:
    job = _job(
        "AI Builder - Agentic Systems",
        location="Remote US",
        description="Build agents, prompt systems, RAG, and orchestration platforms.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification in {"near_fit", "high_fit"}
    assert "Agentic systems overlap" in fit.viability_reasons


def test_infrastructure_sre_role_remains_low_fit() -> None:
    job = _job(
        "Infrastructure SRE",
        location="Remote US",
        description="Own Kubernetes, infrastructure reliability, distributed systems, and low latency systems.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_kubernetes_backend_role_remains_low_fit() -> None:
    job = _job(
        "Backend Engineer",
        location="Remote US",
        description="Build distributed systems with Kubernetes and networking stack focus.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_ai_workflow_platform_role_can_be_apply_now() -> None:
    job = _job(
        "AI Workflow Platform Product Engineer",
        location="Remote US",
        description="3+ years building AI-native workflow systems, orchestration, and internal AI tooling.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level == "apply_now"


def test_product_engineer_with_10_plus_years_is_stretch_or_skip() -> None:
    job = _job(
        "Product Engineer",
        location="Remote US",
        description="Requires 10+ years building AI tooling and workflow automation systems.",
    )
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level in {"stretch", "skip"}
    assert "Remote US role matches target geography" in fit.viability_reasons


def test_las_vegas_hybrid_viability_is_apply_now_or_review() -> None:
    job = _job("Product Manager", location="Las Vegas, NV", description="Own product roadmap.")
    job.workplace_type = "Hybrid"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level in {"apply_now", "review"}


def test_blank_remote_viability_is_review() -> None:
    job = _job("Product Manager", location="", description="Own product roadmap.")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level == "review"


def test_cursor_blank_location_is_known_limitation_review() -> None:
    job = _job("Product Manager", location="", description="Own product roadmap.", company="cursor")
    job.source = "ashby"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"
    assert fit.viability_level != "apply_now"
    assert "Location unavailable from source; manual review required" in fit.viability_reasons


def test_remote_canada_only_viability_is_skip() -> None:
    job = _job("Product Manager", location="Remote - Canada only", description="Own product roadmap.")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert fit.viability_level == "skip"
    assert "Remote role limited to non-US geography" in fit.viability_reasons


def test_remote_usa_normalization_is_eligible() -> None:
    job = _job("Product Manager", location="Remote USA", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.normalized_country == "US"
    assert job.normalized_location_type == "remote"
    assert job.geographic_eligibility == "eligible"
    assert fit.viability_level == "apply_now"


def test_remote_us_and_canada_is_review() -> None:
    job = _job("Product Manager", location="Remote US & Canada", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"
    assert fit.viability_level == "review"


def test_foster_city_hybrid_is_ineligible() -> None:
    job = _job("Product Manager", location="Foster City, CA (Hybrid)", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.normalized_city == "Foster City"
    assert job.normalized_state == "CA"
    assert job.normalized_location_type == "hybrid"
    assert job.geographic_eligibility == "ineligible"
    assert "Hybrid role outside Las Vegas/Nevada" in fit.viability_reasons


def test_las_vegas_hybrid_is_eligible() -> None:
    job = _job("Product Manager", location="Las Vegas, NV (Hybrid)", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.normalized_state == "NV"
    assert job.geographic_eligibility == "eligible"
    assert fit.viability_level in {"apply_now", "review"}


def test_mexico_argentina_peru_is_ineligible() -> None:
    job = _job("Product Manager", location="Mexico; Argentina; Peru", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert fit.viability_level == "skip"


def test_remote_unspecified_is_review() -> None:
    job = _job("Product Manager", location="Remote", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"
    assert fit.viability_level == "review"


def test_europe_remote_is_ineligible() -> None:
    job = _job("Product Manager", location="Remote Europe")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert "Remote role restricted to Europe" in fit.viability_reasons


def test_western_europe_remote_is_ineligible() -> None:
    job = _job("Product Manager", location="Remote Western Europe")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert "Remote role restricted to Europe" in fit.viability_reasons


def test_latam_apac_japan_and_country_remote_rules() -> None:
    for raw in ("Remote LATAM", "Remote APAC", "Remote Japan", "Remote Mexico", "Remote Argentina", "Remote Peru"):
        job = _job("Product Manager", location=raw)
        job.workplace_type = "Remote"
        fit = score_job(job, TARGET_PROFILE)
        assert job.geographic_eligibility == "ineligible"
        assert fit.viability_level == "skip"




def test_united_kingdom_remote_is_ineligible() -> None:
    job = _job("Product Manager", location="Remote United Kingdom")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert "Remote role restricted to United Kingdom" in fit.viability_reasons


def test_australia_remote_is_ineligible() -> None:
    job = _job("Product Manager", location="Remote Australia")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert "Remote role restricted to Australia / ANZ" in fit.viability_reasons


def test_anz_remote_is_ineligible() -> None:
    job = _job("Product Manager", location="Remote ANZ")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "ineligible"
    assert "Remote role restricted to Australia / ANZ" in fit.viability_reasons

def test_north_america_remote_is_review() -> None:
    job = _job("Product Manager", location="Remote North America")
    job.workplace_type = "Remote"
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "review"
    assert "Remote North America requires manual review" in fit.viability_reasons


def test_geographic_eligibility_tightened_cases() -> None:
    cases = [
        ("San Francisco", "", "ineligible", "Location-specific role outside Las Vegas/Nevada"),
        ("San Francisco", "Onsite", "ineligible", "Onsite role outside Las Vegas/Nevada"),
        ("In-Office", "", "ineligible", "In-office role outside Las Vegas/Nevada"),
        ("SF, NY, SEA, Remote-US", "Remote", "eligible", "Remote US role matches target geography"),
        ("San Francisco, US Remote", "Remote", "eligible", "Remote US role matches target geography"),
        ("Las Vegas In-Office", "", "eligible", "Location aligns with target geography"),
        ("San Francisco, CA", "Onsite", "ineligible", "Onsite role outside Las Vegas/Nevada"),
        ("San Francisco, CA", "Hybrid", "ineligible", "Hybrid role outside Las Vegas/Nevada"),
        ("Dublin, Ireland", "Remote", "ineligible", "Remote role limited to non-US geography"),
        ("UAE", "Remote", "ineligible", "Remote role limited to non-US geography"),
        ("Poland", "Remote", "ineligible", "Remote role limited to non-US geography"),
        ("Bangalore, India", "Remote", "ineligible", "Location outside target geography"),
        ("Barcelona, Spain", "Remote", "ineligible", "Remote role limited to non-US geography"),
        ("Remote North America", "Remote", "review", "Remote North America requires manual review"),
        ("Remote-US", "Remote", "eligible", "Remote US role matches target geography"),
        ("Las Vegas, NV", "Hybrid", "eligible", "Location aligns with target geography"),
    ]
    for location, workplace, expected_eligibility, expected_reason in cases:
        job = _job("Product Manager", location=location, description="Own product roadmap.")
        job.workplace_type = workplace
        fit = score_job(job, TARGET_PROFILE)
        assert job.geographic_eligibility == expected_eligibility
        assert expected_reason in fit.viability_reasons


def test_unknown_location_type_with_outside_nevada_red_flag_is_ineligible() -> None:
    job = _job("Product Manager", location="San Francisco")
    job.workplace_type = ""
    fit = score_job(job, TARGET_PROFILE)
    assert job.normalized_location_type == "unknown"
    assert "Onsite or location-specific US role outside Las Vegas/Nevada" in fit.red_flags
    assert job.geographic_eligibility == "ineligible"

def test_india_city_country_terms_are_ineligible() -> None:
    for raw in ("Delhi", "New Delhi", "India", "Bangalore"):
        job = _job("Product Manager", location=raw, description="Own product roadmap.")
        fit = score_job(job, TARGET_PROFILE)
        assert job.geographic_eligibility == "ineligible"
        assert fit.viability_level == "skip"
        assert "Location outside target geography" in fit.viability_reasons


def test_united_states_location_is_not_auto_ineligible() -> None:
    job = _job("Product Manager", location="United States", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility in {"review", "eligible"}
    assert fit.viability_level != "skip"


def test_us_remote_is_eligible() -> None:
    job = _job("Product Manager", location="US Remote", description="Own product roadmap.")
    fit = score_job(job, TARGET_PROFILE)
    assert job.geographic_eligibility == "eligible"
    assert fit.viability_level == "apply_now"


def test_forward_deployed_dach_keeps_role_fit_but_geography_blocks_actionability() -> None:
    job = _job(
        title="Forward Deployed Engineer, GTM, DACH",
        location="Remote",
        description="Build applied AI agents for enterprise deployment, integrations, and customer workflows.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert fit.classification == "high_fit"
    assert job.geographic_eligibility == "ineligible"
    assert fit.viability_level == "skip"
    assert "DACH region role may not be US eligible" in fit.red_flags


def test_forward_deployed_emea_keeps_role_fit_but_requires_geography_review() -> None:
    job = _job(
        title="Forward Deployed Engineer, EMEA",
        location="Remote",
        description="Build applied AI agents for enterprise deployment, integrations, and customer workflows.",
    )

    fit = score_job(job, TARGET_PROFILE)

    assert fit.classification == "high_fit"
    assert job.geographic_eligibility in {"review", "ineligible"}
    assert fit.viability_level != "apply_now"
