"""
Extractive summarizer: produces summary paragraph, bullet points, and
a "why it matters" line for each article.

This is entirely local (no LLM calls) using the raw article's title,
summary/description from the feed, category, and topic matches.

Strategy:
  summary:          clean up and truncate the feed description (1-2 sentences)
  bullets:          extract 2-4 key phrases or sentence fragments
  why_it_matters:   template-based sentence using category + source type
"""

from __future__ import annotations

import re
from typing import Optional

from .utils import truncate, first_sentences


# ─── Why-it-matters templates ─────────────────────────────────────────────────

_WHY_TEMPLATES: dict[str, str] = {
    "model_releases": (
        "New model releases directly affect which AI capabilities are available "
        "to developers and organizations, setting the competitive benchmark."
    ),
    "product_launches": (
        "Product launches determine what AI capabilities reach end users and "
        "how quickly developers can build on them."
    ),
    "agents_tooling": (
        "Advances in agents and developer tooling accelerate how quickly AI "
        "systems can take autonomous action and be integrated into workflows."
    ),
    "research": (
        "Research findings shape the direction of the field and often preview "
        "capabilities or risks that will reach products within months to years."
    ),
    "infrastructure": (
        "Infrastructure and compute developments set the pace of AI training and "
        "deployment, with major chip or cloud shifts having broad industry effects."
    ),
    "policy_regulation": (
        "Policy and regulatory moves directly affect where and how AI can be "
        "deployed, with potential to reshape competitive dynamics globally."
    ),
    "safety_evals": (
        "Safety findings and evaluations establish trust baselines for deployment "
        "and influence regulatory posture toward frontier systems."
    ),
    "funding_ma": (
        "Significant funding and M&A activity signals strategic priorities and "
        "can concentrate resources, talent, and capabilities at specific labs."
    ),
    "enterprise_adoption": (
        "Enterprise adoption signals which AI capabilities are crossing the "
        "reliability threshold for high-stakes business use cases."
    ),
    "other": (
        "This development may have broader implications for AI progress, "
        "adoption, or the competitive landscape."
    ),
}


def _why_it_matters(category: str, source_type: str, title: str) -> str:
    base = _WHY_TEMPLATES.get(category, _WHY_TEMPLATES["other"])
    if source_type == "official":
        prefix = "From a primary source: "
    else:
        prefix = ""
    return prefix + base


# ─── Summarizer ───────────────────────────────────────────────────────────────


class Summarizer:

    def summarize(
        self,
        title: str,
        raw_summary: Optional[str],
        category: str,
        source_type: str,
        topic_matches: list[str],
    ) -> tuple[str, list[str], str]:
        """
        Returns (summary_paragraph, bullets, why_it_matters).
        """
        # Summary paragraph
        if raw_summary and len(raw_summary.strip()) > 40:
            text = raw_summary.strip()
            # Take up to 2 sentences, max 400 chars
            summary = truncate(first_sentences(text, 2), 400)
        else:
            # Fall back to a generic descriptor from the title
            summary = f"{title.strip()}."

        # Bullet points: extract key phrases
        bullets = self._extract_bullets(title, raw_summary or "", topic_matches)

        # Why it matters
        why = _why_it_matters(category, source_type, title)

        return summary, bullets, why

    def _extract_bullets(
        self,
        title: str,
        text: str,
        topic_matches: list[str],
    ) -> list[str]:
        """
        Extract 2-4 bullet points from available text.
        Prefers short complete sentences; falls back to phrases.
        """
        bullets: list[str] = []

        # Candidate sentences from body text
        if text:
            sentences = re.split(r"(?<=[.!?])\s+", text.strip())
            # Pick sentences that look informative (not too short, not too long)
            for sent in sentences:
                sent = sent.strip()
                if 30 <= len(sent) <= 200:
                    bullets.append(sent)
                if len(bullets) >= 4:
                    break

        # If we got fewer than 2 bullets, extract from title
        if len(bullets) < 2:
            # Split title on common separators to get sub-phrases
            parts = re.split(r"[:\-–|,]", title)
            for part in parts:
                part = part.strip()
                if len(part) > 15:
                    cleaned = part.strip().rstrip(".")
                    if cleaned not in bullets:
                        bullets.append(cleaned)

        # Ensure minimum 2, maximum 4
        bullets = bullets[:4]
        if not bullets:
            bullets = [title.strip()]

        return bullets
