"""CLI entry point for job-fit-agent."""

from job_fit_agent.collectors.greenhouse import GreenhouseCollector


def main() -> None:
    collector = GreenhouseCollector()
    companies = ["openai", "anthropic", "duolingo", "notion", "stripe"]

    for company in companies:
        jobs = collector.fetch_jobs(company)
        for job in jobs:
            print(f"{job.title} | {job.company} | {job.location} | {job.url}")


if __name__ == "__main__":
    main()
