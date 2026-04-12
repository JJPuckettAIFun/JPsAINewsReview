"""Shared utility functions."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse, urlencode, parse_qs


BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Query params that are tracking-only and should be stripped during normalization
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "ref", "referrer", "source", "fbclid", "gclid", "mc_cid", "mc_eid",
    "_ga", "mc_eid", "yclid",
}


def normalize_url(url: str) -> str:
    """Remove tracking params, normalize scheme/host/path, strip trailing slash."""
    try:
        parsed = urlparse(url.strip())
        # Lowercase scheme and host
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        # Strip tracking query params
        qs = parse_qs(parsed.query, keep_blank_values=False)
        clean_qs = {k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS}
        query = urlencode(clean_qs, doseq=True) if clean_qs else ""
        # Drop fragment
        return urlunparse((scheme, netloc, path, "", query, ""))
    except Exception:
        return url.strip()


def url_fingerprint(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_fingerprint(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode()).hexdigest()[:16]


def parse_date(value: str) -> Optional[datetime]:
    """
    Try to parse a date string in several common formats.
    Returns a UTC-aware datetime or None.
    """
    if not value:
        return None
    # feedparser gives us a time.struct_time or already parsed
    # This handles ISO 8601, RFC 2822, and common variants
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            continue
    return None


def struct_time_to_datetime(st) -> Optional[datetime]:
    """Convert feedparser's time.struct_time to a UTC datetime."""
    import calendar
    import time
    try:
        ts = calendar.timegm(st)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def days_ago(n: int) -> datetime:
    from datetime import timedelta
    return utcnow() - timedelta(days=n)


def parse_since_arg(value: str) -> Optional[datetime]:
    """
    Parse CLI --since argument like '14d', '2h', '30m'.
    Returns a UTC datetime representing that many units ago.
    """
    from datetime import timedelta
    m = re.fullmatch(r"(\d+)([dhm])", value.strip().lower())
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    if unit == "d":
        return utcnow() - timedelta(days=n)
    elif unit == "h":
        return utcnow() - timedelta(hours=n)
    elif unit == "m":
        return utcnow() - timedelta(minutes=n)
    return None


def truncate(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "..."


def first_sentences(text: str, n: int = 3) -> str:
    """Extract first n sentences from text."""
    # Simple sentence splitter on . ! ?
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:n])
