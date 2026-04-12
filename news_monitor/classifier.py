"""
Classify articles into categories and compute topic keyword matches.

Classification is keyword-based using config/topics.yaml.
Each article is assigned:
  - category:       best-matching topic category
  - topic_matches:  list of matched topic IDs (for scoring)

If no topic matches, the article is assigned to "other".
"""

from __future__ import annotations

import re
from collections import Counter

from .config import AppConfig, TopicConfig
from .models import RawArticle


# ─── Classifier ───────────────────────────────────────────────────────────────


class Classifier:
    def __init__(self, config: AppConfig):
        self.topics = config.topics
        # Pre-compile keyword patterns for efficiency
        self._patterns: dict[str, list[re.Pattern]] = {}
        for topic in self.topics:
            self._patterns[topic.id] = [
                re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)
                for kw in topic.keywords
            ]

    def classify(
        self,
        title: str,
        summary: str = "",
        tags: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """
        Returns (category, matched_topic_ids).

        Searches title (weighted 2x), summary (weighted 1x), and tags.
        Returns the category of the topic with the most keyword hits.
        """
        text_title = title
        text_body = summary or ""
        text_tags = " ".join(tags or [])

        # Count weighted hits per topic
        topic_hits: Counter[str] = Counter()
        for topic in self.topics:
            patterns = self._patterns[topic.id]
            hits = 0
            for pattern in patterns:
                # Title counts double
                hits += len(pattern.findall(text_title)) * 2
                hits += len(pattern.findall(text_body))
                hits += len(pattern.findall(text_tags))
            if hits > 0:
                topic_hits[topic.id] += hits

        if not topic_hits:
            return "other", []

        # Best topic by hit count
        best_topic_id = topic_hits.most_common(1)[0][0]
        best_topic = self._get_topic(best_topic_id)
        category = best_topic.category if best_topic else "other"

        # All matched topics (any non-zero hit)
        matched_ids = [tid for tid, count in topic_hits.items() if count > 0]

        return category, matched_ids

    def is_ai_relevant(
        self,
        title: str,
        summary: str = "",
        tags: list[str] | None = None,
        min_matches: int = 1,
    ) -> bool:
        """
        Returns True if the article has at least min_matches topic keyword hits.
        Used to filter general feeds like Semafor.
        """
        _, matched = self.classify(title, summary, tags)
        return len(matched) >= min_matches

    def _get_topic(self, topic_id: str) -> TopicConfig | None:
        for t in self.topics:
            if t.id == topic_id:
                return t
        return None
