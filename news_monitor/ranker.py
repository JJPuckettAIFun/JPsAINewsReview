"""
Scoring model for article noteworthiness.

Score: 0 to 100 (integer), composed of five additive components:

  Component                  Max    Notes
  ─────────────────────────  ───    ────────────────────────────────────────
  Source trust weight         20    source.trust_weight * 20
  Official source bonus       15    +15 if source_type == "official"
  Topic relevance             30    weighted keyword hits, capped at 30
  Multi-source coverage       20    (num_covering_sources - 1) * 10, cap 20
  Novelty                     15    +15 if article is new this run

  Total max                  100

Labels:
  critical  >= 80
  high      >= 65
  medium    >= 50
  low       <  50
"""

from __future__ import annotations

import math
from typing import Optional

from .config import AppConfig, SourceConfig
from .models import RawArticle


LABEL_THRESHOLDS = [
    (80, "critical"),
    (65, "high"),
    (50, "medium"),
    (0,  "low"),
]


def score_to_label(score: int) -> str:
    for threshold, label in LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return "low"


# ─── Ranker ───────────────────────────────────────────────────────────────────


class Ranker:
    def __init__(self, config: AppConfig):
        self.config = config

    def score(
        self,
        article: RawArticle,
        source_cfg: SourceConfig,
        topic_matches: list[str],
        is_new: bool,
        num_covering_sources: int = 1,
    ) -> tuple[int, str]:
        """
        Compute (score, label) for a single article.

        Args:
          article:               the raw article
          source_cfg:            its source configuration
          topic_matches:         list of matched topic IDs from classifier
          is_new:                True if URL not seen before
          num_covering_sources:  how many sources are reporting this event
        """
        score = 0

        # Component 1: source trust weight (0-20)
        trust_score = round(source_cfg.trust_weight * 20)
        score += trust_score

        # Component 2: official source bonus (0-15)
        if source_cfg.source_type == "official":
            score += 15

        # Component 3: topic relevance (0-30)
        relevance = self._topic_relevance(topic_matches)
        score += min(30, relevance)

        # Component 4: multi-source coverage (0-20)
        coverage_bonus = min(20, (num_covering_sources - 1) * 10)
        score += coverage_bonus

        # Component 5: novelty (0-15)
        if is_new:
            score += 15

        score = max(0, min(100, score))
        label = score_to_label(score)
        return score, label

    def _topic_relevance(self, topic_matches: list[str]) -> int:
        """
        Map matched topic IDs to a relevance score (0-30).
        High-weight topics (weight > 1.3) contribute more.
        """
        if not topic_matches:
            return 0

        topic_map = {t.id: t for t in self.config.topics}
        total_weight = sum(
            topic_map[tid].weight
            for tid in topic_matches
            if tid in topic_map
        )

        # Scale: 1 low-weight match -> ~8pts, 1 high-weight match -> ~12pts
        # Multiple matches accumulate up to the 30pt cap
        base = min(30, int(total_weight * 8))
        return base
