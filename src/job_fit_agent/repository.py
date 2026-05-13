"""SQLite repository for job persistence and deduplication."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from job_fit_agent.models import FitScore, JobPosting

DB_PATH = Path("data/jobs.sqlite")

VALID_STATUSES = {"new", "interested", "applying", "applied", "interviewing", "rejected", "archived"}


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
                location_raw TEXT DEFAULT "",
                normalized_country TEXT DEFAULT "",
                normalized_state TEXT DEFAULT "",
                normalized_city TEXT DEFAULT "",
                normalized_location_type TEXT DEFAULT "",
                geographic_eligibility TEXT DEFAULT "review",
                workplace_type TEXT,
                department TEXT,
                team TEXT,
                url TEXT UNIQUE,
                classification TEXT,
                role_family TEXT,
                score INTEGER,
                viability_score INTEGER DEFAULT 0,
                viability_level TEXT DEFAULT "review",
                viability_reasons TEXT DEFAULT "[]",
                reasons TEXT,
                red_flags TEXT,
                first_seen_at TEXT,
                last_seen_at TEXT,
                status TEXT DEFAULT "new",
                notes TEXT DEFAULT ""
            )
            """
        )




def _ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "status" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT "new"')
        conn.execute('UPDATE jobs SET status = "new" WHERE status IS NULL')
    if "notes" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN notes TEXT DEFAULT ""')
        conn.execute('UPDATE jobs SET notes = "" WHERE notes IS NULL')
    if "viability_score" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN viability_score INTEGER DEFAULT 0')
    if "viability_level" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN viability_level TEXT DEFAULT "review"')
    if "viability_reasons" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN viability_reasons TEXT DEFAULT "[]"')
    if "location_raw" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN location_raw TEXT DEFAULT ""')
    if "normalized_country" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN normalized_country TEXT DEFAULT ""')
    if "normalized_state" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN normalized_state TEXT DEFAULT ""')
    if "normalized_city" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN normalized_city TEXT DEFAULT ""')
    if "normalized_location_type" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN normalized_location_type TEXT DEFAULT ""')
    if "geographic_eligibility" not in columns:
        conn.execute('ALTER TABLE jobs ADD COLUMN geographic_eligibility TEXT DEFAULT "review"')


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}.")

def job_exists(url: str, db_path: Path = DB_PATH) -> bool:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        row = conn.execute("SELECT 1 FROM jobs WHERE url = ?", (url,)).fetchone()
    return row is not None


def upsert_job(job: JobPosting, fit: FitScore, db_path: Path = DB_PATH) -> UpsertResult:
    now = _utc_now_iso()
    reasons = json.dumps(fit.reasons)
    red_flags = json.dumps(fit.red_flags)
    viability_reasons = json.dumps(fit.viability_reasons)

    with _connect(db_path) as conn:
        _ensure_schema(conn)
        existing = conn.execute(
            "SELECT classification, role_family, score, viability_score, viability_level, viability_reasons, reasons, red_flags FROM jobs WHERE url = ?",
            (job.url,),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO jobs (
                    source, company, title, location, location_raw, normalized_country, normalized_state, normalized_city, normalized_location_type, geographic_eligibility, workplace_type, department, team,
                    url, classification, role_family, score, viability_score, viability_level, viability_reasons, reasons, red_flags, first_seen_at, last_seen_at, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.source,
                    job.company,
                    job.title,
                    job.location,
                    job.location_raw,
                    job.normalized_country,
                    job.normalized_state,
                    job.normalized_city,
                    job.normalized_location_type,
                    job.geographic_eligibility,
                    job.workplace_type,
                    job.department,
                    job.team,
                    job.url,
                    fit.classification,
                    fit.role_family,
                    fit.total_score,
                    fit.viability_score,
                    fit.viability_level,
                    viability_reasons,
                    reasons,
                    red_flags,
                    now,
                    now,
                    "new",
                    "",
                ),
            )
            return UpsertResult(is_new=True, updated=False, skipped_duplicate=False)

        changed = any(
            [
                existing["classification"] != fit.classification,
                existing["role_family"] != fit.role_family,
                existing["score"] != fit.total_score,
                existing["viability_score"] != fit.viability_score,
                existing["viability_level"] != fit.viability_level,
                existing["viability_reasons"] != viability_reasons,
                existing["reasons"] != reasons,
                existing["red_flags"] != red_flags,
            ]
        )

        conn.execute(
            """
            UPDATE jobs
            SET source = ?, company = ?, title = ?, location = ?, location_raw = ?, normalized_country = ?, normalized_state = ?, normalized_city = ?, normalized_location_type = ?, geographic_eligibility = ?, workplace_type = ?,
                department = ?, team = ?, classification = ?, role_family = ?, score = ?,
                viability_score = ?, viability_level = ?, viability_reasons = ?, reasons = ?, red_flags = ?, last_seen_at = ?
            WHERE url = ?
            """,
            (
                job.source,
                job.company,
                job.title,
                job.location,
                job.location_raw,
                job.normalized_country,
                job.normalized_state,
                job.normalized_city,
                job.normalized_location_type,
                job.geographic_eligibility,
                job.workplace_type,
                job.department,
                job.team,
                fit.classification,
                fit.role_family,
                fit.total_score,
                fit.viability_score,
                fit.viability_level,
                viability_reasons,
                reasons,
                red_flags,
                now,
                job.url,
            ),
        )

    return UpsertResult(is_new=False, updated=changed, skipped_duplicate=not changed)


def get_new_jobs(db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM jobs WHERE first_seen_at = last_seen_at ORDER BY score DESC, last_seen_at DESC"
        ).fetchall()
    return rows


def get_top_jobs(limit: int = 20, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY score DESC, last_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return rows




def get_jobs_by_status(status: str, limit: int = 50, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    _validate_status(status)
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY score DESC, last_seen_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return rows
def update_status(job_id: int, status: str, db_path: Path = DB_PATH) -> None:
    _validate_status(status)
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        result = conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        if result.rowcount == 0:
            raise ValueError(f"Job id {job_id} not found.")


def update_notes(job_id: int, notes: str, db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        result = conn.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))
        if result.rowcount == 0:
            raise ValueError(f"Job id {job_id} not found.")


def get_job_by_id(job_id: int, db_path: Path = DB_PATH) -> sqlite3.Row | None:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_top_jobs_by_classification(classification: str, limit: int = 10, db_path: Path = DB_PATH) -> list[sqlite3.Row]:
    with _connect(db_path) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT * FROM jobs
            WHERE classification = ? AND status != "archived"
            ORDER BY score DESC, last_seen_at DESC
            LIMIT ?
            """,
            (classification, limit),
        ).fetchall()
    return rows
