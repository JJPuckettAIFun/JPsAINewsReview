"""Tests for deduplication and clustering logic."""

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from news_monitor.dedupe import filter_seen, cluster_articles, _title_tokens
from news_monitor.models import RawArticle, Article


def make_raw(title, url, source_id="src1"):
    return RawArticle(
        source_id=source_id,
        source_name="Test Source",
        url=url,
        title=title,
        published_at=datetime(2026, 4, 10, tzinfo=timezone.utc),
    )


def make_article(title, url, source_id="src1", source_type="reported",
                 trust_weight=0.8, score=60, label="medium", published_at=None):
    from news_monitor.utils import normalize_url, normalize_title
    url_norm = normalize_url(url)
    title_norm = normalize_title(title)
    return Article(
        id=Article.make_id(url_norm),
        source_id=source_id,
        source_name="Test Source",
        source_type=source_type,
        trust_weight=trust_weight,
        url=url,
        url_normalized=url_norm,
        title=title,
        title_normalized=title_norm,
        published_at=published_at or datetime(2026, 4, 10, tzinfo=timezone.utc),
        summary="A test summary.",
        bullets=["Point one"],
        why_it_matters="It matters because.",
        category="research",
        topic_matches=["research"],
        score=score,
        label=label,
        is_new=True,
    )


class TestFilterSeen:

    def test_new_article_passes_through(self):
        articles = [make_raw("New OpenAI Model Released", "https://openai.com/news/model")]
        new, seen = filter_seen(articles, set(), set())
        assert len(new) == 1
        assert len(seen) == 0

    def test_seen_url_filtered(self):
        from news_monitor.utils import normalize_url, url_fingerprint
        url = "https://openai.com/news/model"
        fp = url_fingerprint(normalize_url(url))
        articles = [make_raw("New OpenAI Model Released", url)]
        new, seen = filter_seen(articles, {fp}, set())
        assert len(new) == 0
        assert len(seen) == 1

    def test_seen_title_filtered(self):
        import hashlib
        from news_monitor.utils import normalize_title
        title = "New OpenAI Model Released"
        title_fp = hashlib.sha256(normalize_title(title).encode()).hexdigest()[:16]
        articles = [make_raw(title, "https://openai.com/news/model2")]
        new, seen = filter_seen(articles, set(), {title_fp})
        assert len(new) == 0
        assert len(seen) == 1

    def test_dedup_within_batch(self):
        """Same URL from two sources should deduplicate within the batch."""
        articles = [
            make_raw("OpenAI Model", "https://openai.com/news/model", "src1"),
            make_raw("OpenAI Model", "https://openai.com/news/model", "src2"),
        ]
        new, seen = filter_seen(articles, set(), set())
        assert len(new) == 1

    def test_multiple_new_articles_all_pass(self):
        articles = [
            make_raw("Article One", "https://example.com/1"),
            make_raw("Article Two", "https://example.com/2"),
            make_raw("Article Three", "https://example.com/3"),
        ]
        new, seen = filter_seen(articles, set(), set())
        assert len(new) == 3
        assert len(seen) == 0


class TestClusterArticles:

    def test_identical_titles_cluster(self):
        articles = [
            make_article("OpenAI Releases GPT-5 Model", "https://openai.com/gpt5", source_id="src1"),
            make_article("OpenAI Releases GPT-5 Model", "https://tc.com/openai-gpt5", source_id="src2"),
        ]
        clusters = cluster_articles(articles)
        # Should merge into one cluster
        assert len(clusters) == 1
        assert len(clusters[0].all_articles) == 2

    def test_unrelated_articles_separate_clusters(self):
        articles = [
            make_article("OpenAI GPT-5 Release", "https://openai.com/gpt5"),
            make_article("EU Passes AI Regulation Bill", "https://ec.europa.eu/ai-act"),
            make_article("NVIDIA H200 Chip Production Begins", "https://nvidia.com/h200"),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 3

    def test_official_source_preferred_as_canonical(self):
        articles = [
            make_article("Claude 4 Released", "https://tc.com/claude4",
                        source_id="techcrunch", source_type="reported", trust_weight=0.85),
            make_article("Claude 4 Released", "https://anthropic.com/claude4",
                        source_id="anthropic", source_type="official", trust_weight=1.0),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        assert clusters[0].canonical.source_type == "official"

    def test_higher_trust_wins_when_both_reported(self):
        articles = [
            make_article("AI Funding Round", "https://tc.com/funding",
                        source_id="tc", source_type="reported", trust_weight=0.85),
            make_article("AI Funding Round", "https://wired.com/funding",
                        source_id="wired", source_type="reported", trust_weight=0.80),
        ]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        # TechCrunch has higher trust weight
        assert clusters[0].canonical.trust_weight == 0.85

    def test_single_article_creates_single_cluster(self):
        articles = [make_article("Solo Article", "https://example.com/solo")]
        clusters = cluster_articles(articles)
        assert len(clusters) == 1
        assert len(clusters[0].related) == 0

    def test_empty_input(self):
        assert cluster_articles([]) == []

    def test_clusters_sorted_by_score(self):
        articles = [
            make_article("Low Importance", "https://ex.com/low", score=30, label="low"),
            make_article("Critical News", "https://ex.com/critical", score=90, label="critical"),
            make_article("Medium News", "https://ex.com/medium", score=55, label="medium"),
        ]
        clusters = cluster_articles(articles)
        scores = [c.score for c in clusters]
        assert scores == sorted(scores, reverse=True)


class TestTitleTokens:

    def test_removes_stopwords(self):
        tokens = _title_tokens("the new model is available")
        assert "the" not in tokens
        assert "is" not in tokens

    def test_keeps_meaningful_words(self):
        tokens = _title_tokens("OpenAI releases multimodal model")
        assert "openai" in tokens
        assert "releases" in tokens
        assert "multimodal" in tokens

    def test_short_words_excluded(self):
        tokens = _title_tokens("AI in 2026")
        # "AI" is 2 chars (< 3), "in" is a stopword
        assert "in" not in tokens
