"""SQLite storage for postings, alert subscriptions and dispatch history.

A single file database keeps the whole deployment portable: deleting
`acide_storage.db` is the "wipe everything" path promised in the Privacy
Policy.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from . import paths
from .models import (
    AlertFilters,
    AlertSubscription,
    Job,
    JobEvaluation,
    JobFacets,
    JobPage,
    RawPosting,
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                    TEXT PRIMARY KEY,
    external_id           TEXT NOT NULL,
    source_type           TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL,
    company               TEXT NOT NULL,
    location              TEXT NOT NULL DEFAULT '',
    seniority             TEXT NOT NULL DEFAULT 'Mid-Level',
    category              TEXT NOT NULL DEFAULT 'General',
    years_experience_min  INTEGER NOT NULL DEFAULT 0,
    date_posted           TEXT,
    rate                  TEXT NOT NULL DEFAULT 'Yearly',
    currency              TEXT NOT NULL DEFAULT 'USD',
    amount                REAL NOT NULL DEFAULT 0.0,
    experience_fit_score  INTEGER NOT NULL DEFAULT 0,
    interest_fit_score    INTEGER NOT NULL DEFAULT 0,
    category_type         TEXT NOT NULL DEFAULT 'Unrelated',
    transferable_skills   TEXT NOT NULL DEFAULT '[]',
    skills_to_learn       TEXT NOT NULL DEFAULT '[]',
    alert_summary         TEXT NOT NULL DEFAULT '',
    apply_url             TEXT NOT NULL,
    saved                 INTEGER NOT NULL DEFAULT 0,
    dismissed             INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_scores
    ON jobs (experience_fit_score DESC, interest_fit_score DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs (company);
CREATE INDEX IF NOT EXISTS idx_jobs_category  ON jobs (category);
CREATE INDEX IF NOT EXISTS idx_jobs_posted    ON jobs (date_posted);
CREATE INDEX IF NOT EXISTS idx_jobs_saved     ON jobs (saved);

CREATE TABLE IF NOT EXISTS alert_subscriptions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT NOT NULL,
    filter_json  TEXT NOT NULL DEFAULT '{}',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_active ON alert_subscriptions (active);

-- Guarantees a subscriber is never emailed the same posting twice.
CREATE TABLE IF NOT EXISTS alert_dispatches (
    subscription_id INTEGER NOT NULL,
    job_id          TEXT NOT NULL,
    sent_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (subscription_id, job_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_local = threading.local()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Yield a per-thread connection with foreign keys and WAL enabled."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        paths.ensure_dirs()
        conn = sqlite3.connect(str(paths.DB_PATH), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def reset_connection() -> None:
    """Drop this thread's cached handle (used by tests switching DB paths)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
def job_id_for(source_type: str, company: str, external_id: str) -> str:
    """Stable identity for a posting across re-indexing runs."""
    slug = "-".join(part.strip().lower().replace(" ", "-") for part in (source_type, company))
    return f"{slug}:{external_id}"


def known_external_ids(source_type: str, company: str) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT external_id FROM jobs WHERE source_type = ? AND company = ?",
            (source_type, company),
        ).fetchall()
    return {row["external_id"] for row in rows}


def upsert_job(posting: RawPosting, evaluation: JobEvaluation) -> str:
    """Insert or refresh a scored posting, preserving user-owned flags."""
    job_id = job_id_for(posting.source_type, posting.company, posting.external_id)
    # Compensation published by the ATS beats whatever the model guessed.
    amount = posting.amount if posting.amount else evaluation.amount
    currency = posting.currency or evaluation.currency
    rate = posting.rate or evaluation.rate
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, external_id, source_type, title, company, location, seniority,
                category, years_experience_min, date_posted, rate, currency, amount,
                experience_fit_score, interest_fit_score, category_type,
                transferable_skills, skills_to_learn, alert_summary, apply_url
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                location = excluded.location,
                seniority = excluded.seniority,
                category = excluded.category,
                years_experience_min = excluded.years_experience_min,
                date_posted = excluded.date_posted,
                rate = excluded.rate,
                currency = excluded.currency,
                amount = excluded.amount,
                experience_fit_score = excluded.experience_fit_score,
                interest_fit_score = excluded.interest_fit_score,
                category_type = excluded.category_type,
                transferable_skills = excluded.transferable_skills,
                skills_to_learn = excluded.skills_to_learn,
                alert_summary = excluded.alert_summary,
                apply_url = excluded.apply_url,
                updated_at = datetime('now')
            """,
            (
                job_id,
                posting.external_id,
                posting.source_type,
                posting.title,
                posting.company,
                posting.location,
                evaluation.seniority,
                evaluation.category,
                evaluation.years_experience_min,
                posting.date_posted,
                rate,
                currency,
                amount,
                evaluation.experience_fit_score,
                evaluation.interest_fit_score,
                evaluation.category_type,
                json.dumps(evaluation.transferable_skills),
                json.dumps(evaluation.skills_to_learn),
                evaluation.alert_summary,
                posting.apply_url,
            ),
        )
    return job_id


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        seniority=row["seniority"],
        category=row["category"],
        years_experience_min=row["years_experience_min"],
        date_posted=row["date_posted"],
        rate=row["rate"],
        currency=row["currency"],
        amount=row["amount"],
        experience_fit_score=row["experience_fit_score"],
        interest_fit_score=row["interest_fit_score"],
        category_type=row["category_type"],
        transferable_skills=json.loads(row["transferable_skills"] or "[]"),
        skills_to_learn=json.loads(row["skills_to_learn"] or "[]"),
        alert_summary=row["alert_summary"],
        apply_url=row["apply_url"],
        source_type=row["source_type"],
        saved=bool(row["saved"]),
        dismissed=bool(row["dismissed"]),
        created_at=row["created_at"],
    )


def _posted_since(window: str) -> str | None:
    """Translate the `When ...` dropdown into an ISO cutoff date."""
    deltas = {"today": 1, "3days": 3, "week": 7, "month": 30}
    days = deltas.get(window)
    if days is None:
        return None
    return (datetime.now(UTC) - timedelta(days=days)).date().isoformat()


_YEARS_BUCKETS = {"0-2": (0, 2), "3-5": (3, 5), "6-9": (6, 9), "10+": (10, 99)}


def build_job_query(
    *,
    search: str | None = None,
    location: str | None = None,
    remote: str | None = None,
    category: str | None = None,
    seniority: str | None = None,
    company: str | None = None,
    years_experience: str | None = None,
    posted_within: str | None = None,
    category_type: str | None = None,
    min_amount: float | None = None,
    currency: str | None = None,
    rate: str | None = None,
    match_posting_currency: bool = False,
    saved_only: bool = False,
    include_dismissed: bool = False,
    min_experience_fit: int | None = None,
    min_interest_fit: int | None = None,
    newer_than_id_rowid: str | None = None,
) -> tuple[str, list[Any]]:
    """Compose the WHERE clause shared by listing, counting and alerting.

    Returned as a fragment plus parameters so the same predicate can back the
    grid, the facet counts and the alert daemon without drifting apart.
    """
    from .compensation import normalise_amount  # local import: avoids a cycle

    clauses: list[str] = ["1=1"]
    params: list[Any] = []

    if not include_dismissed:
        clauses.append("dismissed = 0")
    if saved_only:
        clauses.append("saved = 1")

    if search:
        clauses.append(
            "(title LIKE ? OR company LIKE ? OR category LIKE ? OR alert_summary LIKE ?)"
        )
        needle = f"%{search}%"
        params.extend([needle] * 4)
    if location:
        clauses.append("location LIKE ?")
        params.append(f"%{location}%")
    if remote:
        # "Remote from ..." matches either an explicit remote posting or one
        # that names the requested region.
        clauses.append("(location LIKE '%remote%' AND location LIKE ?)")
        params.append(f"%{remote}%")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if seniority:
        clauses.append("seniority = ?")
        params.append(seniority)
    if company:
        clauses.append("company LIKE ?")
        params.append(f"%{company}%")
    if category_type:
        clauses.append("category_type = ?")
        params.append(category_type)
    if min_experience_fit is not None:
        clauses.append("experience_fit_score >= ?")
        params.append(min_experience_fit)
    if min_interest_fit is not None:
        clauses.append("interest_fit_score >= ?")
        params.append(min_interest_fit)

    if years_experience in _YEARS_BUCKETS:
        low, high = _YEARS_BUCKETS[years_experience]
        clauses.append("years_experience_min BETWEEN ? AND ?")
        params.extend([low, high])

    cutoff = _posted_since(posted_within) if posted_within else None
    if cutoff:
        clauses.append("(date_posted IS NULL OR date_posted >= ?)")
        params.append(cutoff)

    if min_amount is not None and min_amount > 0:
        target_rate = rate or "Yearly"
        target_currency = (currency or "USD").upper()
        if match_posting_currency:
            # Compare like for like: only postings quoted in the same currency
            # are eligible, so no exchange-rate assumption enters the filter.
            clauses.append("currency = ?")
            params.append(target_currency)
        # A posting with no published compensation is kept rather than hidden;
        # ATS feeds omit salary far more often than they publish it.
        threshold_sql, threshold_params = normalise_amount(
            min_amount, target_rate, target_currency, match_posting_currency
        )
        clauses.append(f"(amount = 0 OR {threshold_sql})")
        params.extend(threshold_params)

    return " AND ".join(clauses), params


def list_jobs(
    *,
    limit: int = 60,
    offset: int = 0,
    sort: str = "fit",
    **filters: Any,
) -> JobPage:
    where, params = build_job_query(**filters)
    order = {
        "fit": "MAX(experience_fit_score, interest_fit_score) DESC, date_posted DESC",
        "recent": "date_posted DESC, created_at DESC",
        "experience": "experience_fit_score DESC, interest_fit_score DESC",
        "interest": "interest_fit_score DESC, experience_fit_score DESC",
        "compensation": "amount DESC",
    }.get(sort, "MAX(experience_fit_score, interest_fit_score) DESC, date_posted DESC")

    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM jobs WHERE {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM jobs WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

    items = [_row_to_job(row) for row in rows]
    return JobPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(items) < total,
    )


def get_job(job_id: str) -> Job | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def set_job_flag(job_id: str, field: str, value: bool | None) -> Job | None:
    """Toggle (`value=None`) or set the `saved` / `dismissed` flag."""
    if field not in {"saved", "dismissed"}:
        raise ValueError(f"unsupported flag: {field}")
    with connect() as conn:
        if value is None:
            conn.execute(f"UPDATE jobs SET {field} = NOT {field} WHERE id = ?", (job_id,))
        else:
            conn.execute(f"UPDATE jobs SET {field} = ? WHERE id = ?", (int(value), job_id))
    return get_job(job_id)


def facets() -> JobFacets:
    with connect() as conn:
        def distinct(column: str) -> list[str]:
            rows = conn.execute(
                f"SELECT DISTINCT {column} AS v FROM jobs "
                f"WHERE dismissed = 0 AND {column} IS NOT NULL AND {column} != '' "
                f"ORDER BY v COLLATE NOCASE"
            ).fetchall()
            return [row["v"] for row in rows]

        return JobFacets(
            categories=distinct("category"),
            companies=distinct("company"),
            seniorities=distinct("seniority"),
            currencies=distinct("currency"),
        )


def delete_all_jobs() -> int:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM alert_dispatches")
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Alert subscriptions
# ---------------------------------------------------------------------------
def _row_to_alert(row: sqlite3.Row) -> AlertSubscription:
    return AlertSubscription(
        id=row["id"],
        email=row["email"],
        filters=AlertFilters(**json.loads(row["filter_json"] or "{}")),
        active=bool(row["active"]),
        created_at=row["created_at"],
        last_sent_at=row["last_sent_at"],
    )


def create_alert(email: str, filters: AlertFilters) -> AlertSubscription:
    payload = json.dumps(filters.model_dump(exclude_none=True))
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO alert_subscriptions (email, filter_json) VALUES (?, ?)",
            (email, payload),
        )
        row = conn.execute(
            "SELECT * FROM alert_subscriptions WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_alert(row)


def list_alerts(email: str | None = None, active_only: bool = False) -> list[AlertSubscription]:
    query = "SELECT * FROM alert_subscriptions WHERE 1=1"
    params: list[Any] = []
    if email:
        query += " AND email = ?"
        params.append(email)
    if active_only:
        query += " AND active = 1"
    query += " ORDER BY created_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_alert(row) for row in rows]


def set_alert_active(alert_id: int, active: bool) -> AlertSubscription | None:
    with connect() as conn:
        conn.execute(
            "UPDATE alert_subscriptions SET active = ? WHERE id = ?", (int(active), alert_id)
        )
        row = conn.execute(
            "SELECT * FROM alert_subscriptions WHERE id = ?", (alert_id,)
        ).fetchone()
    return _row_to_alert(row) if row else None


def delete_alert(alert_id: int) -> bool:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM alert_subscriptions WHERE id = ?", (alert_id,))
        conn.execute("DELETE FROM alert_dispatches WHERE subscription_id = ?", (alert_id,))
    return cursor.rowcount > 0


def delete_alerts_for_email(email: str) -> int:
    """Erasure request: drop every subscription tied to an address."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id FROM alert_subscriptions WHERE email = ?", (email,)
        ).fetchall()
        ids = [row["id"] for row in rows]
        conn.execute("DELETE FROM alert_subscriptions WHERE email = ?", (email,))
        conn.executemany(
            "DELETE FROM alert_dispatches WHERE subscription_id = ?", [(i,) for i in ids]
        )
    return len(ids)


def undispatched_jobs(subscription: AlertSubscription, limit: int = 25) -> list[Job]:
    """Jobs matching a subscription that have never been emailed to it."""
    filters = subscription.filters.model_dump(exclude_none=True)
    filters.pop("posted_within", None)  # the dispatch ledger already bounds this
    where, params = build_job_query(**filters)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT j.* FROM jobs j
            WHERE {where}
              AND NOT EXISTS (
                  SELECT 1 FROM alert_dispatches d
                  WHERE d.subscription_id = ? AND d.job_id = j.id
              )
            ORDER BY MAX(j.experience_fit_score, j.interest_fit_score) DESC
            LIMIT ?
            """,
            [*params, subscription.id, limit],
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def record_dispatch(subscription_id: int, job_ids: Iterable[str]) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_dispatches (subscription_id, job_id) VALUES (?, ?)",
            [(subscription_id, job_id) for job_id in job_ids],
        )
        conn.execute(
            "UPDATE alert_subscriptions SET last_sent_at = datetime('now') WHERE id = ?",
            (subscription_id,),
        )
