"""
HTTP fetchers for RSS feeds and HTML pages.

Two fetcher classes:
  RSSFetcher  - uses feedparser; handles both plain and UA-required feeds
  HTMLFetcher - uses requests + retries; returns raw HTML string

Both degrade gracefully: return (data, error_string) tuples so callers
can update source health without raising.
"""

from __future__ import annotations

import io
import re
import time
from typing import Optional

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import BROWSER_UA


# ─── XML sanitizer ────────────────────────────────────────────────────────────

# Regex matching characters that are illegal in XML 1.0
_ILLEGAL_XML_CHARS = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F"   # C0 control chars (except \t \n \r)
    r"\uFFFE\uFFFF]"                        # non-characters
)

def _sanitize_feed_bytes(raw: bytes) -> bytes:
    """
    Best-effort cleanup of a raw RSS/Atom response before handing it to
    feedparser.  Handles the three most common causes of bozo errors:

    1. Illegal XML control characters (causes "not well-formed (invalid token)")
    2. Bare & ampersands outside CDATA  (causes "not well-formed (invalid token)")
    3. HTML content-type on an XML body (causes "is not an XML media type")

    Returns cleaned bytes that feedparser can parse as a string/file-like.
    """
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return raw

    # 1. Strip illegal XML control characters
    text = _ILLEGAL_XML_CHARS.sub("", text)

    # 2. Fix bare & that aren't already an entity or inside CDATA
    #    Replace & not followed by #/word chars + semicolon with &amp;
    text = re.sub(r"&(?!(?:#\d+|#x[\da-fA-F]+|[\w]+);)", "&amp;", text)

    return text.encode("utf-8")


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

    For sources marked requires_user_agent=True we always use a browser UA.
    When feedparser marks a feed as bozo (malformed XML) with no entries, we
    fall back to fetching the raw bytes with requests, sanitizing them, and
    re-parsing — this recovers most real-world malformed feeds.
    """

    TIMEOUT = 20  # seconds

    def __init__(self):
        self._session = _make_session()

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
        # Always use browser UA — many feeds block plain Python/feedparser UA
        request_headers = {"User-Agent": BROWSER_UA}

        try:
            parsed = feedparser.parse(
                feed_url,
                request_headers=request_headers,
                etag=etag,
                modified=modified,
            )
        except Exception as exc:
            return None, f"feedparser exception: {exc}"

        # Check HTTP status if available
        status = parsed.get("status", 200)
        if status in (401, 403):
            return None, f"HTTP {status}: access denied"
        if status == 404:
            return None, "HTTP 404: feed not found"
        if status == 304:
            return parsed, None
        if status >= 400:
            return None, f"HTTP {status}"

        # Happy path — valid feed with entries
        if not parsed.get("bozo") and parsed.get("entries"):
            return parsed, None

        # Bozo feed with entries: feedparser parsed it despite errors — use it
        if parsed.get("bozo") and parsed.get("entries"):
            return parsed, None

        # Bozo feed with NO entries: try raw fetch + sanitize fallback
        original_err = str(parsed.get("bozo_exception", "malformed feed"))
        sanitized = self._fetch_and_sanitize(feed_url)
        if sanitized is not None:
            try:
                parsed2 = feedparser.parse(io.BytesIO(sanitized))
                if parsed2.get("entries"):
                    return parsed2, None
            except Exception:
                pass

        # Nothing worked
        if not parsed.get("entries"):
            return None, f"feed parse error: {original_err}"

        return None, "feed returned 0 entries"

    def _fetch_and_sanitize(self, url: str) -> Optional[bytes]:
        """Fetch raw feed bytes via requests and sanitize for re-parsing."""
        try:
            resp = self._session.get(url, timeout=self.TIMEOUT)
            if resp.status_code != 200:
                return None
            return _sanitize_feed_bytes(resp.content)
        except Exception:
            return None


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


# ─── Article content fetcher ──────────────────────────────────────────────────


class ArticleContentFetcher:
    """
    Fetch the full HTML of individual article pages for richer summarization.

    Designed for best-effort use in the pipeline:
      - Short timeout (no retries) so failures are fast
      - Always uses a browser User-Agent (most article pages require it)
      - Returns (html_string, error_message); errors are non-fatal
    """

    TIMEOUT = 10  # seconds — fast fail; don't slow the run

    def __init__(self):
        # Lightweight session: no retry adapter (speed > resilience here)
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })

    def fetch(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """
        Returns (html_string, error_message).  error_message is None on success.
        """
        try:
            resp = self._session.get(url, timeout=self.TIMEOUT, allow_redirects=True)
        except requests.exceptions.Timeout:
            return None, "timeout"
        except requests.exceptions.ConnectionError:
            return None, "connection error"
        except requests.exceptions.RequestException as exc:
            return None, str(exc)

        if resp.status_code in (401, 403):
            return None, f"HTTP {resp.status_code}: access denied"
        if resp.status_code == 404:
            return None, "HTTP 404"
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"

        content_type = resp.headers.get("Content-Type", "")
        if "text/html" not in content_type and "application/xhtml" not in content_type:
            return None, f"unexpected content-type: {content_type}"

        return resp.text, None

    def close(self):
        self._session.close()
