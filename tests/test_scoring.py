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


def test_london_product_manager_gets_location_red_flag() -> None:
    job = _job(
        title="Senior Product Manager",
        location="London",
        description="Drive analytics and experimentation",
    )

    fit = score_job(job, TARGET_PROFILE)
    assert any("Excluded location" in flag for flag in fit.red_flags)


def test_remote_us_product_analytics_scores_higher_than_onsite_ny_only() -> None:
    remote_job = _job(
        title="Product Analytics Manager",
        location="Remote US",
        description="Own analytics, experimentation, funnel, dashboard strategy.",
    )
    onsite_job = _job(
        title="Product Manager",
        location="Onsite - New York only",
        description="General product role without analytics focus.",
    )

    remote_fit = score_job(remote_job, TARGET_PROFILE)
    onsite_fit = score_job(onsite_job, TARGET_PROFILE)

    assert remote_fit.total_score > onsite_fit.total_score


def test_excluded_location_lowers_score() -> None:
    base = _job(
        title="Product Manager",
        location="Remote US",
        description="AI analytics and experimentation",
    )
    excluded = _job(
        title="Product Manager",
        location="London",
        description="AI analytics and experimentation",
    )

    base_fit = score_job(base, TARGET_PROFILE)
    excluded_fit = score_job(excluded, TARGET_PROFILE)

    assert excluded_fit.total_score < base_fit.total_score


def test_preferred_location_increases_score() -> None:
    preferred = _job(
        title="Product Manager",
        location="Hybrid - United States",
        description="AI analytics and experimentation",
    )
    neutral = _job(
        title="Product Manager",
        location="Onsite",
        description="AI analytics and experimentation",
    )

    preferred_fit = score_job(preferred, TARGET_PROFILE)
    neutral_fit = score_job(neutral, TARGET_PROFILE)

    assert preferred_fit.total_score > neutral_fit.total_score
