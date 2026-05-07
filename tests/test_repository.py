from job_fit_agent.models import FitScore, JobPosting
from job_fit_agent.repository import get_new_jobs, get_top_jobs, initialize, job_exists, upsert_job


def _job(url: str, title: str = "Product Manager") -> JobPosting:
    return JobPosting(
        source="greenhouse",
        company="openai",
        title=title,
        location="Remote",
        url=url,
        description="AI",
    )


def _fit(score: int, classification: str = "high_fit") -> FitScore:
    return FitScore(total_score=score, classification=classification, role_family="product", reasons=["a"], red_flags=[])


def test_insert_new_job(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    result = upsert_job(_job("https://example.com/1"), _fit(90), db)

    assert result.is_new is True
    assert job_exists("https://example.com/1", db)


def test_update_duplicate_url(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1", title="PM"), _fit(80, "near_fit"), db)
    result = upsert_job(_job("https://example.com/1", title="Senior PM"), _fit(95, "high_fit"), db)

    assert result.is_new is False
    assert result.updated is True


def test_retrieval_ordering(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(75), db)
    upsert_job(_job("https://example.com/2"), _fit(98), db)

    jobs = get_top_jobs(limit=2, db_path=db)
    assert jobs[0]["url"] == "https://example.com/2"
    assert jobs[1]["url"] == "https://example.com/1"


def test_get_new_jobs(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(80), db)
    upsert_job(_job("https://example.com/2"), _fit(50, "near_fit"), db)
    upsert_job(_job("https://example.com/1"), _fit(80), db)

    jobs = get_new_jobs(db)
    urls = [row["url"] for row in jobs]
    assert "https://example.com/2" in urls
    assert "https://example.com/1" not in urls


def test_get_top_jobs_by_classification_returns_only_requested_classification(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/h1", title="High 1"), _fit(95, "high_fit"), db)
    upsert_job(_job("https://example.com/h2", title="High 2"), _fit(90, "high_fit"), db)
    upsert_job(_job("https://example.com/n1", title="Near 1"), _fit(85, "near_fit"), db)

    from job_fit_agent.repository import get_top_jobs_by_classification

    high_rows = get_top_jobs_by_classification("high_fit", limit=10, db_path=db)
    assert len(high_rows) == 2
    assert all(row["classification"] == "high_fit" for row in high_rows)
    assert [row["url"] for row in high_rows] == ["https://example.com/h1", "https://example.com/h2"]
