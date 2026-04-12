"""
Deduplication and event clustering.

Four-layer dedupe approach (in order of cost):
  1. Normalized URL match       - O(1) hash lookup
  2. Normalized title match     - O(1) hash lookup
  3. Fuzzy title similarity     - O(n) difflib SequenceMatcher
  4. Keyword/entity overlap     - O(n*k) bag-of-words Jaccard

After dedup, related articles are clustered into EventCluster objects.
The "best" (canonical) article is selected by:
  1. Prefer official source_type
  2. Then highest trust_weight
  3. Then most recent published_at

All operations are deterministic and require no external state beyond the
existing seen_fingerprints sets passed in from storage.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Optional

from .models import RawArticle, Article, EventCluster
from .utils import normalize_url, normalize_title, url_fingerprint


# ─── Constants ───────────────────────────────────────────────────────────────

FUZZY_THRESHOLD = 0.72      # SequenceMatcher ratio above which titles are "same"
JACCARD_THRESHOLD = 0.30    # Jaccard similarity above which articles share an event


# ─── Article dedup ────────────────────────────────────────────────────────────


def filter_seen(
    articles: list[RawArticle],
    seen_url_fps: set[str],
    seen_title_fps: set[str],
) -> tuple[list[RawArticle], list[RawArticle]]:
    """
    Split articles into (new, already_seen).

    Layer 1: exact normalized URL fingerprint
    Layer 2: exact normalized title fingerprint
    """
    new_articles: list[RawArticle] = []
    seen_articles: list[RawArticle] = []

    # Track within this batch too, to avoid duplicates from two sources
    batch_url_fps: set[str] = set()
    batch_title_fps: set[str] = set()

    for article in articles:
        url_fp = url_fingerprint(normalize_url(article.url))
        title_fp = hashlib.sha256(
            normalize_title(article.title).encode()
        ).hexdigest()[:16]

        if (
            url_fp in seen_url_fps
            or url_fp in batch_url_fps
            or title_fp in seen_title_fps
            or title_fp in batch_title_fps
        ):
            seen_articles.append(article)
        else:
            new_articles.append(article)
            batch_url_fps.add(url_fp)
            batch_title_fps.add(title_fp)

    return new_articles, seen_articles


# ─── Clustering ───────────────────────────────────────────────────────────────


def cluster_articles(articles: list[Article]) -> list[EventCluster]:
    """
    Group articles that cover the same underlying event.

    Two articles are merged into the same cluster if they pass the
    fuzzy-title threshold OR the keyword-jaccard threshold.

    Returns a list of EventCluster objects, each with a canonical article.
    """
    if not articles:
        return []

    # Union-find for grouping
    parent = list(range(len(articles)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        pi, pj = find(i), find(j)
        if pi != pj:
            parent[pj] = pi

    # Pre-tokenize all titles for Jaccard
    token_sets = [_title_tokens(a.title) for a in articles]
    normalized_titles = [a.title_normalized for a in articles]

    n = len(articles)
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue

            # Layer 3: fuzzy title similarity
            ratio = difflib.SequenceMatcher(
                None, normalized_titles[i], normalized_titles[j]
            ).ratio()
            if ratio >= FUZZY_THRESHOLD:
                union(i, j)
                continue

            # Layer 4: keyword Jaccard
            if token_sets[i] and token_sets[j]:
                inter = len(token_sets[i] & token_sets[j])
                uni = len(token_sets[i] | token_sets[j])
                if uni > 0 and (inter / uni) >= JACCARD_THRESHOLD:
                    union(i, j)

    # Collect groups
    groups: dict[int, list[Article]] = {}
    for i, article in enumerate(articles):
        root = find(i)
        groups.setdefault(root, []).append(article)

    # Build EventCluster objects
    clusters: list[EventCluster] = []
    for group_articles in groups.values():
        canonical = _pick_canonical(group_articles)
        related = [a for a in group_articles if a.id != canonical.id]

        # Assign cluster ID and related_urls
        cluster_id = canonical.id
        canonical.cluster_id = cluster_id
        for a in related:
            a.cluster_id = cluster_id
            if a.url not in canonical.related_urls:
                canonical.related_urls.append(a.url)

        max_score = max(a.score for a in group_articles)
        top_label = _score_to_label(max_score)

        clusters.append(EventCluster(
            id=cluster_id,
            canonical=canonical,
            related=related,
            score=max_score,
            label=top_label,
            category=canonical.category,
        ))

    # Sort by score descending
    clusters.sort(key=lambda c: c.score, reverse=True)
    return clusters


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _title_tokens(title: str) -> set[str]:
    """Bag of meaningful words from title (remove stopwords, short tokens)."""
    STOPWORDS = {
        "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or",
        "but", "with", "from", "by", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "it", "its", "this", "that", "as", "new", "how",
        "what", "why", "when", "who", "will", "can", "could", "would", "should",
        "not", "no", "says", "said", "using", "use", "just", "more", "than",
    }
    words = re.findall(r"\b[a-z]{3,}\b", title.lower())
    return {w for w in words if w not in STOPWORDS}


def _pick_canonical(articles: list[Article]) -> Article:
    """
    Choose the canonical (best) article for a cluster.
    Priority: official > highest trust_weight > most recent date.
    """
    def sort_key(a: Article):
        official = 1 if a.source_type == "official" else 0
        trust = a.trust_weight
        ts = a.published_at.timestamp() if a.published_at else 0
        return (official, trust, ts)

    return max(articles, key=sort_key)


def _score_to_label(score: int) -> str:
    if score >= 80:
        return "critical"
    if score >= 65:
        return "high"
    if score >= 50:
        return "medium"
    return "low"
