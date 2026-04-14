"""
Extractive summarizer: produces summary paragraph, bullet points, and
a "why it matters" line for each article.

This is entirely local (no LLM calls) using the raw article's title,
summary/description from the feed, category, and topic matches.

Strategy:
  summary:          3-4 sentences drawn from the feed description
  bullets:          2-4 key takeaways drawn from sentences NOT already
                    used in the summary — so they never duplicate it
  why_it_matters:   template-based sentence using category + source type
"""

from __future__ import annotations

import re
from typing import Optional

from .utils import truncate


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
    prefix = "From a primary source: " if source_type == "official" else ""
    return prefix + base


# ─── Sentence splitter ────────────────────────────────────────────────────────

def _split_sentences(text: str) -> list[str]:
    """Split text into individual sentences, stripping empties."""
    raw = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in raw if len(s.strip()) >= 20]


def _sentences_overlap(a: str, b: str, threshold: float = 0.6) -> bool:
    """
    Return True if sentence b is substantially contained within sentence a.
    Uses word-overlap ratio so near-identical phrasings are caught too.
    """
    words_a = set(re.findall(r"\b\w+\b", a.lower()))
    words_b = set(re.findall(r"\b\w+\b", b.lower()))
    if not words_b:
        return False
    overlap = len(words_a & words_b) / len(words_b)
    return overlap >= threshold


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

        The summary and bullets are always drawn from different sentences so
        they never duplicate each other.
        """
        text = (raw_summary or "").strip()
        sentences = _split_sentences(text) if text else []

        # ── Summary: first 3-4 usable sentences, max 600 chars ───────────────
        summary_sentences: list[str] = []
        for sent in sentences:
            # Skip very short or very long sentences
            if len(sent) < 25 or len(sent) > 400:
                continue
            summary_sentences.append(sent)
            # Stop at 4 sentences or ~600 chars
            if len(summary_sentences) >= 4:
                break
            if sum(len(s) for s in summary_sentences) >= 600:
                break

        if summary_sentences:
            summary = " ".join(summary_sentences)
        else:
            # No usable feed text — build a one-liner from the title
            summary = (
                f"{title.strip().rstrip('.')}. "
                f"See the full article for details."
            )

        # ── Bullets: sentences NOT already used in the summary ───────────────
        summary_set = set(summary_sentences)
        remaining = [s for s in sentences if s not in summary_set]

        bullets: list[str] = []
        for sent in remaining:
            if len(sent) < 30 or len(sent) > 220:
                continue
            # Skip if it heavily overlaps with an already-chosen bullet
            if any(_sentences_overlap(b, sent) for b in bullets):
                continue
            # Skip if it heavily overlaps with the summary itself
            if _sentences_overlap(summary, sent, threshold=0.7):
                continue
            bullets.append(sent)
            if len(bullets) >= 4:
                break

        # ── Fallback bullets from title phrases ──────────────────────────────
        if len(bullets) < 2:
            parts = re.split(r"[:\-–|,]", title)
            for part in parts:
                part = part.strip().rstrip(".")
                if len(part) > 20 and not _sentences_overlap(summary, part, threshold=0.5):
                    if part not in bullets:
                        bullets.append(part)
            # Last resort: a single "Read the full article" note
            if not bullets:
                bullets = ["Read the full article for complete details."]

        bullets = bullets[:4]

        # ── Why it matters ────────────────────────────────────────────────────
        why = _why_it_matters(category, source_type, title)

        return summary, bullets, why
