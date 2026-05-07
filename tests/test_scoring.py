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
