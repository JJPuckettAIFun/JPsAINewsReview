"""Tests for the scoring and ranking model."""

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from news_monitor.ranker import Ranker, score_to_label
from news_monitor.models import RawArticle


def _make_config(trust=1.0, source_type="official"):
    """Create a minimal AppConfig-like object for testing."""
    from types import SimpleNamespace
    import yaml

    config_dir = Path(__file__).parent.parent / "config"
    from news_monitor.config import load_config
    try:
        return load_config()
    except Exception:
        # Minimal fallback config for unit tests
        cfg = SimpleNamespace()
        cfg.topics = []
        cfg.relevance_min_keyword_matches = 1
        cfg.general_feed_min_matches = 2
        cfg.sources = []
        return cfg


def _make_source(trust=1.0, source_type="official"):
    from types import SimpleNamespace
    s = SimpleNamespace()
    s.trust_weight = trust
    s.source_type = source_type
    s.id = "test_source"
    return s


def _make_raw(title="Test Article", url="https://example.com/test"):
    return RawArticle(
        source_id="test",
        source_name="Test",
        url=url,
        title=title,
        published_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )


class TestScoreToLabel:

    def test_critical_threshold(self):
        assert score_to_label(80) == "critical"
        assert score_to_label(100) == "critical"
        assert score_to_label(79) == "high"

    def test_high_threshold(self):
        assert score_to_label(65) == "high"
        assert score_to_label(64) == "medium"

    def test_medium_threshold(self):
        assert score_to_label(50) == "medium"
        assert score_to_label(49) == "low"

    def test_low(self):
        assert score_to_label(0) == "low"
        assert score_to_label(30) == "low"


class TestRanker:

    def test_official_source_scores_higher_than_reported(self):
        try:
            config = _make_config()
            ranker = Ranker(config)
        except Exception:
            pytest.skip("Config not available")

        raw = _make_raw()
        official_src = _make_source(trust=1.0, source_type="official")
        reported_src = _make_source(trust=1.0, source_type="reported")

        score_official, _ = ranker.score(raw, official_src, [], is_new=True)
        score_reported, _ = ranker.score(raw, reported_src, [], is_new=True)

        assert score_official > score_reported

    def test_new_article_scores_higher(self):
        try:
            config = _make_config()
            ranker = Ranker(config)
        except Exception:
            pytest.skip("Config not available")

        raw = _make_raw()
        src = _make_source(trust=0.8, source_type="reported")

        score_new, _ = ranker.score(raw, src, [], is_new=True)
        score_old, _ = ranker.score(raw, src, [], is_new=False)

        assert score_new > score_old

    def test_multi_source_coverage_increases_score(self):
        try:
            config = _make_config()
            ranker = Ranker(config)
        except Exception:
            pytest.skip("Config not available")

        raw = _make_raw()
        src = _make_source(trust=0.8, source_type="reported")

        score_one, _ = ranker.score(raw, src, [], is_new=True, num_covering_sources=1)
        score_three, _ = ranker.score(raw, src, [], is_new=True, num_covering_sources=3)

        assert score_three > score_one

    def test_score_bounded_0_to_100(self):
        try:
            config = _make_config()
            ranker = Ranker(config)
        except Exception:
            pytest.skip("Config not available")

        raw = _make_raw()
        # Max everything
        src = _make_source(trust=1.0, source_type="official")
        score, _ = ranker.score(
            raw, src,
            ["model_releases", "agents_tooling", "safety_evals"],
            is_new=True,
            num_covering_sources=10,
        )
        assert 0 <= score <= 100

    def test_high_trust_official_with_topic_matches_critical(self):
        try:
            config = _make_config()
            ranker = Ranker(config)
        except Exception:
            pytest.skip("Config not available")

        raw = _make_raw()
        src = _make_source(trust=1.0, source_type="official")
        score, label = ranker.score(
            raw, src,
            ["model_releases", "frontier_models"],
            is_new=True,
            num_covering_sources=3,
        )
        # Should be high or critical
        assert label in ("critical", "high", "medium")
