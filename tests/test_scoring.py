from job_fit_agent.config import load_target_profile
from job_fit_agent.models import JobPosting
from job_fit_agent.scoring import score_job

TARGET_PROFILE = load_target_profile()


def _job(title: str, location: str = "Remote", description: str = "") -> JobPosting:
    return JobPosting(
        source="test",
        company="Test Co",
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


def test_remote_technical_product_manager_is_high_fit() -> None:
    job = _job(
        title="Technical Product Manager",
        location="Remote US",
        description="Drive AI experimentation and analytics roadmap.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "high_fit"


def test_remote_engineering_manager_is_low_fit() -> None:
    job = _job(
        title="Engineering Manager",
        location="Remote US",
        description="Manage software delivery and people operations.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


def test_blank_location_gets_unknown_location_red_flag() -> None:
    job = _job(
        title="Product Manager",
        location="",
        description="Own analytics roadmap for product platform.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert "Unknown location" in fit.red_flags


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


def test_remote_product_engineer_is_low_fit() -> None:
    job = _job(
        title="Product Engineer",
        location="Remote US",
        description="Build AI product experiences and experimentation systems.",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert fit.classification == "low_fit"


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
