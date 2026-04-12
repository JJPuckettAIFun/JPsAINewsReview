"""Tests for SQLite state persistence."""

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from news_monitor.storage import (
    init_db, begin_run, finish_run, get_last_successful_run,
    mark_articles_seen, get_seen_fingerprints, clear_seen_state,
)
from news_monitor.models import Article, RunMetadata
from news_monitor.utils import normalize_url, normalize_title


def _tmp_db():
    """Return a temporary database path."""
    return Path(tempfile.mktemp(suffix=".db"))


def _make_article(url, title, score=60):
    url_norm = normalize_url(url)
    title_norm = normalize_title(title)
    return Article(
        id=Article.make_id(url_norm),
        source_id="test_source",
        source_name="Test",
        source_type="official",
        trust_weight=1.0,
        url=url,
        url_normalized=url_norm,
        title=title,
        title_normalized=title_norm,
        published_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
        summary="Summary.",
        bullets=["Point"],
        why_it_matters="Matters.",
        category="research",
        topic_matches=["research"],
        score=score,
        label="medium",
        is_new=True,
    )


class TestRunLifecycle:

    def test_first_run_no_previous(self):
        db = _tmp_db()
        init_db(db)
        last = get_last_successful_run(db)
        assert last is None

    def test_begin_run_returns_id(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)
        assert run_id is not None
        assert len(run_id) > 0

    def test_successful_run_recorded(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)
        meta = RunMetadata(
            run_id=run_id,
            started_at=now,
            finished_at=now,
            status="success",
            window_start=now,
            window_end=now,
            sources_checked=5,
            articles_selected=10,
        )
        finish_run(run_id, meta, db)

        last = get_last_successful_run(db)
        assert last is not None

    def test_failed_run_not_returned_as_last_success(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)
        meta = RunMetadata(
            run_id=run_id,
            started_at=now,
            finished_at=now,
            status="failed",
            window_start=now,
            window_end=now,
        )
        finish_run(run_id, meta, db)

        last = get_last_successful_run(db)
        assert last is None

    def test_in_progress_run_not_returned_as_last_success(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        begin_run(now, now, db)  # never finished
        last = get_last_successful_run(db)
        assert last is None


class TestSeenArticles:

    def test_mark_and_retrieve_fingerprints(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)

        article = _make_article("https://openai.com/news/gpt5", "OpenAI GPT-5")
        mark_articles_seen([article], run_id, db)

        fps = get_seen_fingerprints(db)
        assert article.id in fps

    def test_clear_seen_state(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)

        article = _make_article("https://openai.com/news/gpt5", "OpenAI GPT-5")
        mark_articles_seen([article], run_id, db)

        clear_seen_state(db)
        fps = get_seen_fingerprints(db)
        assert len(fps) == 0

    def test_duplicate_insert_ignored(self):
        """Inserting same article twice should not raise or create duplicate."""
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)

        article = _make_article("https://openai.com/news/gpt5", "OpenAI GPT-5")
        mark_articles_seen([article], run_id, db)
        mark_articles_seen([article], run_id, db)  # second insert

        fps = get_seen_fingerprints(db)
        assert len(fps) == 1

    def test_multiple_articles_tracked(self):
        db = _tmp_db()
        init_db(db)
        now = datetime.now(tz=timezone.utc)
        run_id = begin_run(now, now, db)

        articles = [
            _make_article(f"https://example.com/{i}", f"Article {i}")
            for i in range(5)
        ]
        mark_articles_seen(articles, run_id, db)

        fps = get_seen_fingerprints(db)
        assert len(fps) == 5
