"""
SQLite-backed persistence for run state, seen articles, and source health.

Schema:
  runs           - one row per run, with status and timestamps
  seen_articles  - one row per URL that has been processed
  event_clusters - one row per deduplicated event fingerprint
  source_health  - one row per source, upserted after each fetch

Atomic run pattern:
  1. storage.begin_run() -> writes an in_progress row
  2. storage.record_articles(articles)
  3. storage.finish_run(run_id, status="success")
  If the process crashes, the run stays in_progress and is treated as
  incomplete on next startup (last_successful_run ignores it).
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import Article, RunMetadata, SourceHealth
from .config import STATE_DB


# ─── Connection helper ────────────────────────────────────────────────────────


@contextmanager
def _db(path: Path = STATE_DB):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── Schema ───────────────────────────────────────────────────────────────────


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'in_progress',
    window_start    TEXT,
    window_end      TEXT,
    sources_checked INTEGER DEFAULT 0,
    sources_failed  INTEGER DEFAULT 0,
    candidates_found INTEGER DEFAULT 0,
    articles_selected INTEGER DEFAULT 0,
    report_path     TEXT
);

CREATE TABLE IF NOT EXISTS seen_articles (
    url_fingerprint  TEXT PRIMARY KEY,
    url_normalized   TEXT NOT NULL,
    title_normalized TEXT NOT NULL,
    title_fingerprint TEXT NOT NULL,
    source_id        TEXT NOT NULL,
    first_seen_run   TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    score            INTEGER DEFAULT 0,
    category         TEXT DEFAULT '',
    label            TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS event_clusters (
    cluster_id       TEXT PRIMARY KEY,
    canonical_url    TEXT NOT NULL,
    canonical_title  TEXT NOT NULL,
    category         TEXT NOT NULL,
    first_seen_at    TEXT NOT NULL,
    last_updated_at  TEXT NOT NULL,
    member_urls      TEXT NOT NULL   -- JSON array
);

CREATE TABLE IF NOT EXISTS source_health (
    source_id               TEXT PRIMARY KEY,
    last_checked            TEXT,
    last_success            TEXT,
    last_error              TEXT,
    consecutive_failures    INTEGER DEFAULT 0,
    total_articles_fetched  INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_seen_title ON seen_articles(title_fingerprint);
CREATE INDEX IF NOT EXISTS idx_seen_source ON seen_articles(source_id);
"""


def init_db(path: Path = STATE_DB) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _db(path) as conn:
        conn.executescript(SCHEMA)


# ─── Run management ───────────────────────────────────────────────────────────


def begin_run(
    window_start: datetime,
    window_end: datetime,
    path: Path = STATE_DB,
) -> str:
    """Start a new run, return run_id."""
    run_id = str(uuid.uuid4())[:8]
    now = _iso(datetime.now(tz=timezone.utc))
    with _db(path) as conn:
        conn.execute(
            """INSERT INTO runs (run_id, started_at, status, window_start, window_end)
               VALUES (?, ?, 'in_progress', ?, ?)""",
            (run_id, now, _iso(window_start), _iso(window_end)),
        )
    return run_id


def finish_run(run_id: str, meta: RunMetadata, path: Path = STATE_DB) -> None:
    """Mark a run as finished (success or failed)."""
    with _db(path) as conn:
        conn.execute(
            """UPDATE runs SET
                status=?, finished_at=?,
                sources_checked=?, sources_failed=?,
                candidates_found=?, articles_selected=?,
                report_path=?
               WHERE run_id=?""",
            (
                meta.status,
                _iso(meta.finished_at or datetime.now(tz=timezone.utc)),
                meta.sources_checked,
                meta.sources_failed,
                meta.candidates_found,
                meta.articles_selected,
                meta.report_path,
                run_id,
            ),
        )


def get_last_successful_run(path: Path = STATE_DB) -> Optional[datetime]:
    """
    Return the finished_at datetime of the most recent successful run.

    Any completed run (status='success') advances the window — including
    runs that found articles but none scored above the display threshold.
    The seen-article dedup layer ensures nothing is re-shown regardless of
    the window, so advancing the window on low-result runs is always safe.
    """
    try:
        with _db(path) as conn:
            row = conn.execute(
                """SELECT finished_at FROM runs
                   WHERE status='success'
                   ORDER BY finished_at DESC LIMIT 1"""
            ).fetchone()
            if row and row["finished_at"]:
                return _from_iso(row["finished_at"])
    except Exception:
        pass
    return None


# ─── Article tracking ─────────────────────────────────────────────────────────


def mark_articles_seen(
    articles: list[Article],
    run_id: str,
    path: Path = STATE_DB,
) -> None:
    """Insert new article fingerprints into seen_articles. Skip duplicates."""
    now = _iso(datetime.now(tz=timezone.utc))
    with _db(path) as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO seen_articles
               (url_fingerprint, url_normalized, title_normalized, title_fingerprint,
                source_id, first_seen_run, first_seen_at, score, category, label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    Article.make_id(a.url_normalized),
                    a.url_normalized,
                    a.title_normalized,
                    a.title_normalized,   # using full normalized title as fingerprint too
                    a.source_id,
                    run_id,
                    now,
                    a.score,
                    a.category,
                    a.label,
                )
                for a in articles
            ],
        )


def is_url_seen(url_normalized: str, path: Path = STATE_DB) -> bool:
    with _db(path) as conn:
        row = conn.execute(
            "SELECT 1 FROM seen_articles WHERE url_fingerprint=? LIMIT 1",
            (Article.make_id(url_normalized),),
        ).fetchone()
        return row is not None


def get_seen_fingerprints(path: Path = STATE_DB) -> set[str]:
    """Return all url_fingerprints we've already seen."""
    try:
        with _db(path) as conn:
            rows = conn.execute("SELECT url_fingerprint FROM seen_articles").fetchall()
            return {r["url_fingerprint"] for r in rows}
    except Exception:
        return set()


def get_seen_title_fingerprints(path: Path = STATE_DB) -> set[str]:
    """Return all title_fingerprint values we've already seen."""
    try:
        with _db(path) as conn:
            rows = conn.execute("SELECT title_normalized FROM seen_articles").fetchall()
            return {r["title_normalized"] for r in rows}
    except Exception:
        return set()


# ─── Event clusters ───────────────────────────────────────────────────────────


def upsert_cluster(
    cluster_id: str,
    canonical_url: str,
    canonical_title: str,
    category: str,
    member_urls: list[str],
    path: Path = STATE_DB,
) -> None:
    now = _iso(datetime.now(tz=timezone.utc))
    with _db(path) as conn:
        existing = conn.execute(
            "SELECT first_seen_at, member_urls FROM event_clusters WHERE cluster_id=?",
            (cluster_id,),
        ).fetchone()
        if existing:
            existing_members = json.loads(existing["member_urls"])
            merged = list(dict.fromkeys(existing_members + member_urls))
            conn.execute(
                """UPDATE event_clusters
                   SET canonical_url=?, canonical_title=?, category=?,
                       last_updated_at=?, member_urls=?
                   WHERE cluster_id=?""",
                (canonical_url, canonical_title, category, now,
                 json.dumps(merged), cluster_id),
            )
        else:
            conn.execute(
                """INSERT INTO event_clusters
                   (cluster_id, canonical_url, canonical_title, category,
                    first_seen_at, last_updated_at, member_urls)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cluster_id, canonical_url, canonical_title, category,
                 now, now, json.dumps(member_urls)),
            )


# ─── Source health ────────────────────────────────────────────────────────────


def update_source_health(health: SourceHealth, path: Path = STATE_DB) -> None:
    with _db(path) as conn:
        conn.execute(
            """INSERT INTO source_health
               (source_id, last_checked, last_success, last_error,
                consecutive_failures, total_articles_fetched)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                 last_checked=excluded.last_checked,
                 last_success=COALESCE(excluded.last_success, last_success),
                 last_error=excluded.last_error,
                 consecutive_failures=excluded.consecutive_failures,
                 total_articles_fetched=total_articles_fetched + excluded.total_articles_fetched""",
            (
                health.source_id,
                _iso(health.last_checked),
                _iso(health.last_success),
                health.last_error,
                health.consecutive_failures,
                health.total_articles_fetched,
            ),
        )


def get_known_source_ids(path: Path = STATE_DB) -> set[str]:
    """
    Return source_ids that have at least one successful fetch on record.
    Sources NOT in this set are considered new and will receive a full
    14-day lookback window on their first run.
    """
    try:
        with _db(path) as conn:
            rows = conn.execute(
                "SELECT source_id FROM source_health WHERE last_success IS NOT NULL"
            ).fetchall()
            return {r["source_id"] for r in rows}
    except Exception:
        return set()


def get_all_source_health(path: Path = STATE_DB) -> list[SourceHealth]:
    try:
        with _db(path) as conn:
            rows = conn.execute("SELECT * FROM source_health").fetchall()
            result = []
            for r in rows:
                h = SourceHealth(source_id=r["source_id"])
                h.last_checked = _from_iso(r["last_checked"])
                h.last_success = _from_iso(r["last_success"])
                h.last_error = r["last_error"]
                h.consecutive_failures = r["consecutive_failures"] or 0
                h.total_articles_fetched = r["total_articles_fetched"] or 0
                result.append(h)
            return result
    except Exception:
        return []


def clear_seen_state(path: Path = STATE_DB) -> None:
    """Force full refresh: clear all seen articles and clusters."""
    with _db(path) as conn:
        conn.execute("DELETE FROM seen_articles")
        conn.execute("DELETE FROM event_clusters")


def delete_run(report_path: str, path: Path = STATE_DB) -> dict:
    """
    Delete ALL runs associated with a report file and their seen_articles.

    Multiple runs on the same day share the same report filename (e.g.
    2026-04-16-ai-news-summary.md). Using fetchone() previously left
    stale run records in the DB, causing get_last_successful_run() to
    return a recent timestamp and narrow the window on the next run —
    producing inconsistent article counts. Now all matching records are
    cleared together.

    Always cleans up orphaned seen_articles (rows whose run_id no longer
    exists in the runs table) even if the path lookup fails.

    Returns a dict with keys:
      run_id           - the last deleted run_id (or None if not found)
      articles_removed - total seen_article rows deleted
      found            - True if at least one matching run record was found
    """
    with _db(path) as conn:
        # Find ALL runs sharing this report path (same-day runs reuse the file)
        basename = Path(report_path).name
        rows = conn.execute(
            """SELECT run_id FROM runs
               WHERE report_path = ?
                  OR report_path LIKE ?
                  OR report_path LIKE ?""",
            (report_path, f"%{basename}", f"%/{basename}"),
        ).fetchall()

        run_id = None
        articles_removed = 0

        for row in rows:
            run_id = row["run_id"]
            cur = conn.execute(
                "DELETE FROM seen_articles WHERE first_seen_run = ?",
                (run_id,),
            )
            articles_removed += cur.rowcount
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

        # Always sweep for orphaned seen_articles whose run no longer exists.
        # This catches rows left behind by path-lookup failures or prior bugs.
        cur2 = conn.execute(
            """DELETE FROM seen_articles
               WHERE first_seen_run NOT IN (
                   SELECT run_id FROM runs
               )"""
        )
        articles_removed += cur2.rowcount

    return {
        "found": len(rows) > 0,
        "run_id": run_id,
        "articles_removed": articles_removed,
    }


def list_runs(path: Path = STATE_DB) -> list[dict]:
    """Return all run records ordered newest first."""
    try:
        with _db(path) as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _from_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
