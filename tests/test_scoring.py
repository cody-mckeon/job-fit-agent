from job_fit_agent.models import JobPosting
from job_fit_agent.scoring import score_job


def _job(title: str, location: str = "Remote", description: str = "") -> JobPosting:
    return JobPosting(
        source="test",
        company="Test Co",
        title=title,
        location=location,
        url="https://example.com/job",
        description=description,
    )


def test_product_analytics_role_scores_high() -> None:
    job = _job(
        title="Senior Product Analytics Manager",
        location="Remote US",
        description="Own experimentation and analytics platform strategy for a data product.",
    )

    fit = score_job(job)
    assert fit.total_score >= 45


def test_technical_product_manager_scores_high() -> None:
    job = _job(
        title="Technical Product Manager, AI",
        location="Hybrid - United States",
        description="Build customer-facing web products with machine learning and personalization.",
    )

    fit = score_job(job)
    assert fit.total_score >= 45


def test_treasury_role_scores_low() -> None:
    job = _job(
        title="Treasury Manager",
        location="Onsite",
        description="Lead treasury and finance operations for corporate reporting.",
    )

    fit = score_job(job)
    assert fit.total_score <= 15
    assert fit.red_flags


def test_software_engineer_role_scores_low() -> None:
    job = _job(
        title="Software Engineer",
        location="Onsite",
        description="Backend platform engineering role.",
    )

    fit = score_job(job)
    assert fit.total_score <= 15
    assert fit.red_flags


def test_remote_product_role_gets_location_boost() -> None:
    remote_job = _job(
        title="Product Manager",
        location="Remote US",
        description="Own product analytics roadmap.",
    )
    onsite_job = _job(
        title="Product Manager",
        location="Onsite",
        description="Own product analytics roadmap.",
    )

    remote_fit = score_job(remote_job)
    onsite_fit = score_job(onsite_job)

    assert remote_fit.total_score > onsite_fit.total_score
