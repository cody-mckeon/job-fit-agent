"""CLI entry point for job-fit-agent."""

from job_fit_agent.models import JobPosting
from job_fit_agent.scoring import score_job


def main() -> None:
    sample = JobPosting(
        source="demo",
        company="Example Co",
        title="Product Manager - AI Analytics",
        location="Remote",
        url="https://example.com/jobs/1",
        description="Lead AI-powered product analytics initiatives.",
    )
    fit = score_job(sample)
    print(f"{sample.title} @ {sample.company} -> score={fit.total_score}")


if __name__ == "__main__":
    main()
