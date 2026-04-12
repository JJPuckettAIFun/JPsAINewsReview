"""Tests for URL and title normalization."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from news_monitor.utils import normalize_url, normalize_title, url_fingerprint, title_fingerprint


class TestNormalizeURL:

    def test_strips_tracking_params(self):
        url = "https://example.com/article?utm_source=twitter&utm_medium=social"
        assert normalize_url(url) == "https://example.com/article"

    def test_strips_multiple_tracking_params(self):
        url = "https://tc.com/post?fbclid=abc&gclid=xyz&ref=newsletter"
        result = normalize_url(url)
        assert "fbclid" not in result
        assert "gclid" not in result
        assert "ref" not in result

    def test_preserves_meaningful_params(self):
        url = "https://example.com/search?q=openai&page=2"
        result = normalize_url(url)
        assert "q=openai" in result
        assert "page=2" in result

    def test_removes_trailing_slash(self):
        assert normalize_url("https://example.com/article/") == "https://example.com/article"

    def test_root_path_preserved(self):
        result = normalize_url("https://example.com/")
        assert result in ("https://example.com", "https://example.com/")

    def test_lowercase_scheme_and_host(self):
        url = "HTTPS://Example.COM/Article"
        result = normalize_url(url)
        assert result.startswith("https://example.com")

    def test_strips_fragment(self):
        url = "https://example.com/article#section-2"
        assert "#" not in normalize_url(url)

    def test_idempotent(self):
        url = "https://openai.com/news/rss"
        assert normalize_url(normalize_url(url)) == normalize_url(url)

    def test_techcrunch_url(self):
        url = "https://techcrunch.com/2026/04/12/some-article/?utm_source=feedly"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "techcrunch.com" in result


class TestNormalizeTitle:

    def test_lowercase(self):
        assert normalize_title("OpenAI Releases GPT-5") == "openai releases gpt 5"

    def test_strips_punctuation(self):
        result = normalize_title("Hello, world! This is a test.")
        assert "," not in result
        assert "!" not in result
        assert "." not in result

    def test_collapses_whitespace(self):
        result = normalize_title("  too   many   spaces  ")
        assert "  " not in result
        assert result == result.strip()

    def test_idempotent(self):
        title = "Anthropic: New Claude Model Surpasses GPT-4o"
        assert normalize_title(normalize_title(title)) == normalize_title(title)

    def test_handles_empty_string(self):
        assert normalize_title("") == ""

    def test_unicode_preserved(self):
        # Basic unicode should survive
        result = normalize_title("AI en España")
        assert "a" in result  # 'a' from 'IA' lowercased


class TestFingerprints:

    def test_different_urls_different_fps(self):
        fp1 = url_fingerprint("https://openai.com/news/gpt5")
        fp2 = url_fingerprint("https://openai.com/news/gpt4")
        assert fp1 != fp2

    def test_same_url_same_fp(self):
        assert url_fingerprint("https://example.com/a") == url_fingerprint("https://example.com/a")

    def test_tracking_urls_same_fp(self):
        url1 = "https://techcrunch.com/article"
        url2 = "https://techcrunch.com/article?utm_source=twitter"
        assert url_fingerprint(url1) == url_fingerprint(url2)

    def test_title_fp_length(self):
        fp = title_fingerprint("Some article title here")
        assert len(fp) == 16

    def test_url_fp_length(self):
        fp = url_fingerprint("https://example.com/article")
        assert len(fp) == 16
