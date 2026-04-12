"""
HTTP fetchers for RSS feeds and HTML pages.

Two fetcher classes:
  RSSFetcher  - uses feedparser; handles both plain and UA-required feeds
  HTMLFetcher - uses requests + retries; returns raw HTML string

Both degrade gracefully: return (data, error_string) tuples so callers
can update source health without raising.
"""

from __future__ import annotations

import time
from typing import Optional

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import BROWSER_UA


# ─── Shared session ───────────────────────────────────────────────────────────


def _make_session(user_agent: str = BROWSER_UA) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
    })
    return session


# ─── RSS fetcher ──────────────────────────────────────────────────────────────


class RSSFetcher:
    """
    Fetch and parse an RSS/Atom feed via feedparser.

    feedparser handles most feed formats (RSS 0.9x, 1.0, 2.0, Atom) and is
    lenient on malformed XML. It also handles HTTP redirects and ETags.

    For sources marked requires_user_agent=True, we pass a browser UA through
    feedparser's request_headers argument.
    """

    TIMEOUT = 20  # seconds

    def fetch(
        self,
        feed_url: str,
        requires_ua: bool = False,
        etag: Optional[str] = None,
        modified: Optional[str] = None,
    ) -> tuple[feedparser.FeedParserDict | None, Optional[str]]:
        """
        Returns (parsed_feed, error_message).
        error_message is None on success.
        """
        headers = {}
        if requires_ua:
            headers["User-Agent"] = BROWSER_UA

        try:
            # feedparser.parse() is synchronous; pass request_headers for UA
            parsed = feedparser.parse(
                feed_url,
                request_headers=headers if headers else None,
                etag=etag,
                modified=modified,
            )
        except Exception as exc:
            return None, f"feedparser exception: {exc}"

        # feedparser sets bozo=True on malformed feeds
        if parsed.get("bozo") and not parsed.get("entries"):
            err = str(parsed.get("bozo_exception", "malformed feed"))
            return None, f"feed parse error: {err}"

        # Check HTTP status if available
        status = parsed.get("status", 200)
        if status in (401, 403):
            return None, f"HTTP {status}: access denied"
        if status == 404:
            return None, "HTTP 404: feed not found"
        if status == 304:
            # Not modified since last fetch
            return parsed, None
        if status >= 400:
            return None, f"HTTP {status}"

        if not parsed.get("entries"):
            return None, "feed returned 0 entries"

        return parsed, None


# ─── HTML fetcher ─────────────────────────────────────────────────────────────


class HTMLFetcher:
    """
    Fetch a raw HTML page via requests with retry logic.
    Used for Anthropic (Next.js SSR) pages that return full HTML.
    """

    TIMEOUT = 20

    def __init__(self):
        self._session = _make_session()

    def fetch(
        self,
        url: str,
        requires_ua: bool = False,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Returns (html_string, error_message).
        error_message is None on success.
        """
        headers = {}
        if requires_ua:
            headers["User-Agent"] = BROWSER_UA

        try:
            resp = self._session.get(url, timeout=self.TIMEOUT, headers=headers)
        except requests.exceptions.Timeout:
            return None, "request timed out"
        except requests.exceptions.ConnectionError as e:
            return None, f"connection error: {e}"
        except requests.exceptions.RequestException as e:
            return None, f"request error: {e}"

        if resp.status_code in (401, 403):
            return None, f"HTTP {resp.status_code}: access denied"
        if resp.status_code == 404:
            return None, "HTTP 404: page not found"
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"

        return resp.text, None

    def close(self):
        self._session.close()
