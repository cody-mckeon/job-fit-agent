from job_fit_agent.models import JobPosting
from job_fit_agent.scoring import score_job


def test_strong_ai_product_manager_role_scores_high() -> None:
    job = JobPosting(
        source="test",
        company="AI Corp",
        title="Senior Product Manager, AI",
        location="Remote",
        url="https://example.com/job/ai-pm",
        description="Lead AI roadmap, analytics strategy, and data product decisions.",
    )

    fit = score_job(job)
    assert fit.total_score >= 50


def test_unrelated_role_scores_low() -> None:
    job = JobPosting(
        source="test",
        company="Transit Co",
        title="Delivery Driver",
        location="On-site",
        url="https://example.com/job/driver",
        description="Commercial route driver with logistics and loading tasks.",
    )

    fit = score_job(job)
    assert fit.total_score <= 10
    assert fit.red_flags


def test_remote_analytics_pm_role_scores_high() -> None:
    job = JobPosting(
        source="test",
        company="Metrics Inc",
        title="Product Owner - Web Analytics",
        location="Hybrid / Remote",
        url="https://example.com/job/analytics-po",
        description="Own web analytics platform and product operations workflows.",
    )

    fit = score_job(job)
    assert fit.total_score >= 50
