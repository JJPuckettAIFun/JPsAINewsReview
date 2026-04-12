"""
Data models for the AI News Monitor.

These dataclasses flow through the pipeline:
  RawArticle  -> (normalize, dedupe) -> Article -> (cluster) -> EventCluster
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ─── Raw fetch output ────────────────────────────────────────────────────────


@dataclass
class RawArticle:
    """Article as returned directly by a fetcher/parser, before processing."""

    source_id: str
    source_name: str
    url: str
    title: str
    published_at: Optional[datetime] = None
    summary: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    @property
    def url_fingerprint(self) -> str:
        return hashlib.sha256(self.url.strip().lower().encode()).hexdigest()[:16]

    @property
    def title_fingerprint(self) -> str:
        normalized = _normalize_title(self.title)
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ─── Processed article ───────────────────────────────────────────────────────


@dataclass
class Article:
    """Fully processed, scored, classified article ready for reporting."""

    id: str                        # sha256 fingerprint of normalized URL
    source_id: str
    source_name: str
    source_type: str               # "official" or "reported"
    trust_weight: float
    url: str
    url_normalized: str
    title: str
    title_normalized: str
    published_at: Optional[datetime]
    summary: str                   # extracted/generated summary paragraph
    bullets: list[str]             # 2-4 key points
    why_it_matters: str
    category: str                  # classifier output
    topic_matches: list[str]       # matched topic ids
    score: int                     # 0-100
    label: str                     # critical / high / medium / low
    is_new: bool                   # False if URL was seen in a prior run
    cluster_id: Optional[str] = None
    related_urls: list[str] = field(default_factory=list)

    @classmethod
    def make_id(cls, url_normalized: str) -> str:
        return hashlib.sha256(url_normalized.encode()).hexdigest()[:16]


# ─── Event cluster ───────────────────────────────────────────────────────────


@dataclass
class EventCluster:
    """Group of articles covering the same underlying story or event."""

    id: str                        # fingerprint of canonical article
    canonical: Article             # best/primary source article
    related: list[Article]         # secondary coverage
    score: int                     # highest member score
    label: str                     # label of canonical article
    category: str

    @property
    def all_articles(self) -> list[Article]:
        return [self.canonical] + self.related


# ─── Run state ───────────────────────────────────────────────────────────────


@dataclass
class SourceHealth:
    source_id: str
    last_checked: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    total_articles_fetched: int = 0


@dataclass
class RunMetadata:
    run_id: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    status: str = "in_progress"    # in_progress / success / failed
    window_start: Optional[datetime] = None
    window_end: Optional[datetime] = None
    sources_checked: int = 0
    sources_failed: int = 0
    candidates_found: int = 0
    articles_selected: int = 0
    report_path: Optional[str] = None


# ─── Report output ───────────────────────────────────────────────────────────


@dataclass
class Report:
    metadata: RunMetadata
    top_items: list[EventCluster]        # medium+ by default
    honorable_mentions: list[EventCluster]  # low, or medium if --include-low used
    source_health: list[SourceHealth]
    generated_at: datetime = field(default_factory=datetime.utcnow)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    import re
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t
