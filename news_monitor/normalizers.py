"""
Normalize RawArticle objects into a standard form for deduplication and scoring.

Normalization is idempotent and does not require external state.
"""

from __future__ import annotations

from .models import RawArticle
from .utils import normalize_url, normalize_title, url_fingerprint, title_fingerprint, truncate


def normalize_raw(article: RawArticle) -> RawArticle:
    """
    Normalize a RawArticle in place (returns same object for convenience).
    - Strips/normalizes URL
    - Strips/normalizes title
    - Cleans up summary whitespace
    """
    article.url = article.url.strip()
    article.title = article.title.strip()
    if article.summary:
        article.summary = " ".join(article.summary.split())

    return article


def make_normalized_fields(url: str, title: str) -> tuple[str, str]:
    """Return (url_normalized, title_normalized)."""
    return normalize_url(url), normalize_title(title)


def make_fingerprints(url: str, title: str) -> tuple[str, str]:
    """Return (url_fp, title_fp) - short hex digests."""
    return url_fingerprint(url), title_fingerprint(title)
