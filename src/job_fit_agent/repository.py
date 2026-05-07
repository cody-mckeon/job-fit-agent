"""SQLite repository for job persistence and deduplication."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from job_fit_agent.models import FitScore, JobPosting

DB_PATH = Path("data/jobs.sqlite")


@dataclass
class UpsertResult:
    is_new: bool
    updated: bool
    skipped_duplicate: bool


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def initialize(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                source TEXT,
                company TEXT,
                title TEXT,
                location TEXT,
                workplace_type TEXT,
                department TEXT,
                team TEXT,
                url TEXT UNIQUE,
                classification TEXT,
                role_family TEXT,
                score INTEGER,
                reasons TEXT,
                red_flags TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT
            )
            """
        )


def job_exists(url: str, db_path: Path = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone()
    return row is not None


def upsert_job(job: JobPosting, fit: FitScore, db_path: Path = DB_PATH) -> UpsertResult:
    now = _utc_now_iso()
    reasons = json.dumps(fit.reasons)
    red_flags = json.dumps(fit.red_flags)

    with _connect(db_path) as conn:
        existing = conn.execute(
            "SELECT classification, role_family, score, reasons, red_flags FROM jobs WHERE url = ?",
            (job.url,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO jobs (
                    source, company, title, location, workplace_type, department, team,
                    url, classification, role_family, score, reasons, red_flags, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source,
                    job.company,
                    job.title,
                    job.location,
                    job.workplace_type,
                    job.department,
                    job.team,
                    job.url,
                    fit.classification,
                    fit.role_family,
                    fit.total_score,
                    reasons,
                    red_flags,
                    now,
                    now,
                ),
            )
            return UpsertResult(is_new=True, updated=False, skipped_duplicate=False)

        changed = any(
            [
                existing["classification"] != fit.classification,
                existing["role_family"] != fit.role_family,
                existing["score"] != fit.total_score,
                existing["reasons"] != reasons,
                existing["red_flags"] != red_flags,
            ]
        )

        conn.execute(
            """
            UPDATE jobs
            SET source = ?, company = ?, title = ?, location = ?, workplace_type = ?,
                department = ?, team = ?, classification = ?, role_family = ?, score = ?,
                reasons = ?, red_flags = ?, last_seen_at = ?
            WHERE url = ?
            """,
            (
                job.source,
                job.company,
                job.title,
                job.location,
                job.workplace_type,
                job.department,
                job.team,
                fit.classification,
                fit.role_family,
                fit.total_score,
                reasons,
                red_flags,
                now,
                job.url,
            ),
        )

    return UpsertResult(is_new=False, updated=changed, skipped_duplicate=not changed)


def get_new_jobs(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE first_seen_at = last_seen_at ORDER BY score DESC, last_seen_at DESC"
        ).fetchall()
    return rows


def get_top_jobs(limit: int = 20, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY score DESC, last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows


def get_top_jobs_by_classification(classification: str, limit: int = 10, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE classification = ?
            ORDER BY score DESC, last_seen_at DESC
            LIMIT ?
            """,
            (classification, limit),
        ).fetchall()
    return rows
