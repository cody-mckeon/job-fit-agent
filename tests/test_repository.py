from job_fit_agent.models import FitScore, JobPosting
import pytest

from job_fit_agent.repository import (
    get_job_by_id,
    get_jobs_by_status,
    get_new_jobs,
    get_top_jobs,
    get_top_jobs_by_classification,
    initialize,
    job_exists,
    update_notes,
    update_status,
    upsert_job,
)


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

    high_rows = get_top_jobs_by_classification("high_fit", limit=10, db_path=db)
    near_rows = get_top_jobs_by_classification("near_fit", limit=10, db_path=db)

    assert len(high_rows) == 2
    assert all(row["classification"] == "high_fit" for row in high_rows)
    assert [row["url"] for row in high_rows] == ["https://example.com/h1", "https://example.com/h2"]

    assert len(near_rows) == 1
    assert all(row["classification"] == "near_fit" for row in near_rows)
    assert near_rows[0]["url"] == "https://example.com/n1"


def test_update_status_and_notes_and_get_by_id(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(90), db)
    row = get_top_jobs(limit=1, db_path=db)[0]

    update_status(row["id"], "interested", db)
    update_notes(row["id"], "Strong referral", db)
    updated = get_job_by_id(row["id"], db)

    assert updated is not None
    assert updated["status"] == "interested"
    assert updated["notes"] == "Strong referral"


def test_update_status_rejects_invalid_status(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(90), db)
    row = get_top_jobs(limit=1, db_path=db)[0]

    with pytest.raises(ValueError):
        update_status(row["id"], "invalid", db)


def test_digest_queries_exclude_archived_by_classification(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(95, "high_fit"), db)
    upsert_job(_job("https://example.com/2"), _fit(90, "high_fit"), db)
    rows = get_top_jobs(limit=2, db_path=db)
    update_status(rows[1]["id"], "archived", db)

    visible = get_top_jobs_by_classification("high_fit", db_path=db)
    assert len(visible) == 1


def test_upsert_updates_scoring_fields_and_preserves_status_notes_first_seen(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)

    upsert_job(_job("https://example.com/preserve", title="PM"), _fit(70, "near_fit"), db)
    row = get_top_jobs(limit=1, db_path=db)[0]
    job_id = row["id"]
    first_seen_at = row["first_seen_at"]

    update_status(job_id, "interested", db)
    update_notes(job_id, "keep this note", db)

    new_fit = FitScore(
        total_score=95,
        classification="high_fit",
        role_family="product",
        reasons=["updated reason"],
        red_flags=["old red flag removed"],
    )
    upsert_job(_job("https://example.com/preserve", title="Senior PM"), new_fit, db)

    updated = get_job_by_id(job_id, db)
    assert updated is not None
    assert updated["status"] == "interested"
    assert updated["notes"] == "keep this note"
    assert updated["first_seen_at"] == first_seen_at
    assert updated["classification"] == "high_fit"
    assert updated["score"] == 95
    assert updated["red_flags"] == '["old red flag removed"]'


def test_get_jobs_by_status_returns_only_matching_status(tmp_path) -> None:
    db = tmp_path / "jobs.sqlite"
    initialize(db)
    upsert_job(_job("https://example.com/1"), _fit(90), db)
    upsert_job(_job("https://example.com/2"), _fit(80), db)
    rows = get_top_jobs(limit=2, db_path=db)
    update_status(rows[0]["id"], "applying", db)

    applying = get_jobs_by_status("applying", db_path=db)
    assert len(applying) == 1
    assert applying[0]["status"] == "applying"
