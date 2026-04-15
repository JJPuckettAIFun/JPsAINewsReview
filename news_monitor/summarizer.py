"""
Extractive summarizer: produces summary paragraph, bullet points, and
a "why it matters" line for each article.

This is entirely local (no LLM calls) using the raw article's title,
summary/description from the feed, category, and topic matches.

Strategy:
  summary:          Title used as a lead sentence when feed text is thin;
                    otherwise 3-4 sentences drawn from the feed description.
  bullets:          Drawn from feed sentences NOT already in the summary.
                    When feed text is too short, category- and topic-aware
                    bullets are generated from metadata so the output is
                    always informative rather than a generic "read more".
  why_it_matters:   Template-based sentence using category + source type.
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

# Category-specific detail hints used in fallback bullets
_CATEGORY_DETAIL: dict[str, str] = {
    "model_releases":     "The full article includes benchmark comparisons, pricing, and access details.",
    "product_launches":   "The full article covers availability, pricing, and feature specifics.",
    "research":           "The linked paper or post contains methodology, datasets, and full results.",
    "infrastructure":     "The full article covers technical specs, scale, and deployment timeline.",
    "policy_regulation":  "The full article details regulatory scope, affected parties, and timeline.",
    "safety_evals":       "The full article includes evaluation methodology, risk levels, and mitigations.",
    "funding_ma":         "The full article covers deal terms, valuations, and strategic rationale.",
    "enterprise_adoption":"The full article covers deployment scale, use case, and business impact.",
    "agents_tooling":     "The full article includes capability details, integrations, and availability.",
}


def _why_it_matters(category: str, source_type: str, title: str) -> str:
    base = _WHY_TEMPLATES.get(category, _WHY_TEMPLATES["other"])
    prefix = "From a primary source: " if source_type == "official" else ""
    return prefix + base


# ─── Text cleaning ────────────────────────────────────────────────────────────

# Phrases that indicate a feed is just a teaser/stub — discard them
_STUB_PATTERNS = re.compile(
    r"(read\s+(the\s+)?(full|more|complete)\s+(article|post|story|details?)"
    r"|click\s+here\s+to\s+read"
    r"|continue\s+reading"
    r"|\.{3,}\s*$"
    r"|\[[\.\s]+\])",
    re.IGNORECASE,
)


def _clean_feed_text(text: str) -> str:
    """Strip HTML tags, entity refs, and stub phrases from feed description."""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode common HTML entities
    text = (text
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " "))
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove stub phrases
    text = _STUB_PATTERNS.sub("", text).strip()
    return text


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


# ─── Best sentence picker ────────────────────────────────────────────────────

def _best_sentence(paragraph: str, topic_matches: list[str]) -> Optional[str]:
    """
    Pick the single most informative sentence from a paragraph.
    Scores by: length (longer = more informative), numeric data,
    and overlap with known topic keywords.
    """
    sentences = _split_sentences(paragraph)
    if not sentences:
        # paragraph has no sentence-ending punctuation — return it trimmed
        text = paragraph.strip()
        return text[:260] if len(text) >= 35 else None

    topic_words = set(
        w.lower()
        for t in topic_matches
        for w in re.findall(r"\b\w+\b", t)
    )

    best, best_score = None, -1
    for sent in sentences:
        if len(sent) < 35 or len(sent) > 300:
            continue
        score = 0
        score += min(len(sent), 200) / 10          # length bonus
        score += len(re.findall(r"\d", sent)) * 2   # numeric data bonus
        words = set(re.findall(r"\b\w+\b", sent.lower()))
        score += len(words & topic_words) * 3        # topic keyword bonus
        if score > best_score:
            best, best_score = sent, score

    return best


# ─── Contextual fallback bullets ─────────────────────────────────────────────

def _context_bullets(
    title: str,
    category: str,
    source_type: str,
    topic_matches: list[str],
    summary: str,
    existing_bullets: list[str],
) -> list[str]:
    """
    Generate informative fallback bullets from article metadata.
    Never returns a generic 'read the article' stub as the sole output.
    """
    bullets: list[str] = []

    # 1. Topic areas covered
    if topic_matches:
        clean = [t.replace("_", " ").title() for t in topic_matches[:5]]
        candidate = f"Key topics covered: {', '.join(clean)}."
        if not any(_sentences_overlap(b, candidate) for b in existing_bullets):
            bullets.append(candidate)

    # 2. Source type context
    if source_type == "official":
        bullets.append("This is a direct announcement from the publishing organization.")
    else:
        bullets.append("Reported by an independent news or analysis source.")

    # 3. Category-specific detail hint
    hint = _CATEGORY_DETAIL.get(category)
    if hint and not any(_sentences_overlap(b, hint) for b in existing_bullets + bullets):
        bullets.append(hint)

    return bullets


# ─── Summarizer ───────────────────────────────────────────────────────────────


class Summarizer:

    def summarize(
        self,
        title: str,
        raw_summary: Optional[str],
        category: str,
        source_type: str,
        topic_matches: list[str],
        full_text: Optional[str] = None,
    ) -> tuple[str, list[str], str]:
        """
        Returns (summary_paragraph, bullets, why_it_matters).

        When full_text (fetched from the article page) is available it is
        preferred over the RSS snippet and produces paragraph-level summaries.
        When only the feed snippet is available the title is used as a lead
        sentence and context-aware fallback bullets fill any gaps.
        """
        why = _why_it_matters(category, source_type, title)

        # ── Full-text path ────────────────────────────────────────────────────
        if full_text and len(full_text) >= 300:
            return self._summarize_from_full_text(
                title, full_text, category, source_type, topic_matches, why
            )

        # ── Feed-text path ────────────────────────────────────────────────────
        return self._summarize_from_feed(
            title, raw_summary, category, source_type, topic_matches, why
        )

    # ── Full-text summarization ───────────────────────────────────────────────

    def _summarize_from_full_text(
        self,
        title: str,
        full_text: str,
        category: str,
        source_type: str,
        topic_matches: list[str],
        why: str,
    ) -> tuple[str, list[str], str]:
        """
        Paragraph-level extractive summarization from the full article body.

        Summary:  First 2-3 substantive paragraphs, max ~700 chars.
        Bullets:  Best representative sentence from each subsequent paragraph
                  (scored by length, numbers, and keyword density).
        """
        # Split full_text into paragraphs (double-newline OR very long text chunks)
        raw_paragraphs = [p.strip() for p in re.split(r"\n\n+", full_text)]
        paragraphs = [p for p in raw_paragraphs if len(p) >= 60]

        if not paragraphs:
            # Fallback: treat the whole thing as one blob of sentences
            return self._summarize_from_feed(
                title, full_text, category, source_type, topic_matches, why
            )

        # ── Build summary from first 2-3 paragraphs ───────────────────────────
        summary_paras: list[str] = []
        char_count = 0
        for para in paragraphs[:4]:
            # Truncate individual very-long paragraphs
            para_text = para if len(para) <= 400 else para[:400].rsplit(" ", 1)[0] + "…"
            summary_paras.append(para_text)
            char_count += len(para_text)
            if len(summary_paras) >= 2 and char_count >= 500:
                break
            if len(summary_paras) >= 3:
                break

        summary = " ".join(summary_paras)

        # ── Extract bullet sentences from remaining paragraphs ────────────────
        bullet_pool_paras = paragraphs[len(summary_paras):]
        bullets: list[str] = []

        for para in bullet_pool_paras:
            if len(bullets) >= 4:
                break
            # Pick the best sentence from this paragraph
            candidate = _best_sentence(para, topic_matches)
            if not candidate or len(candidate) < 35:
                continue
            if _sentences_overlap(summary, candidate, threshold=0.65):
                continue
            if any(_sentences_overlap(b, candidate) for b in bullets):
                continue
            bullets.append(candidate)

        # ── Fallback bullets if article was short ─────────────────────────────
        if len(bullets) < 2:
            fallbacks = _context_bullets(
                title, category, source_type, topic_matches, summary, bullets
            )
            for fb in fallbacks:
                if len(bullets) >= 4:
                    break
                bullets.append(fb)

        return summary, bullets[:4], why

    # ── Feed-text summarization ───────────────────────────────────────────────

    def _summarize_from_feed(
        self,
        title: str,
        raw_summary: Optional[str],
        category: str,
        source_type: str,
        topic_matches: list[str],
        why: str,
    ) -> tuple[str, list[str], str]:
        """Sentence-level extractive summarization from RSS feed snippet."""
        text = _clean_feed_text(raw_summary or "")
        sentences = _split_sentences(text) if text else []
        usable = [s for s in sentences if 20 <= len(s) <= 500]
        feed_is_thin = len(usable) < 2

        summary_sentences: list[str] = []

        if feed_is_thin:
            lead = title.strip().rstrip(".")
            summary = f"{lead}. {usable[0]}" if usable else f"{lead}."
        else:
            for sent in usable:
                summary_sentences.append(sent)
                if len(summary_sentences) >= 4:
                    break
                if sum(len(s) for s in summary_sentences) >= 600:
                    break
            summary = " ".join(summary_sentences)

        summary_set = set(summary_sentences)
        remaining = [s for s in sentences if s not in summary_set]

        bullets: list[str] = []
        for sent in remaining:
            if len(sent) < 30 or len(sent) > 260:
                continue
            if any(_sentences_overlap(b, sent) for b in bullets):
                continue
            if _sentences_overlap(summary, sent, threshold=0.7):
                continue
            bullets.append(sent)
            if len(bullets) >= 4:
                break

        if len(bullets) < 2:
            fallbacks = _context_bullets(
                title, category, source_type, topic_matches, summary, bullets
            )
            for fb in fallbacks:
                if len(bullets) >= 4:
                    break
                bullets.append(fb)

        if len(bullets) < 1:
            parts = re.split(r"[:\-–|,]", title)
            for part in parts:
                part = part.strip().rstrip(".")
                if len(part) > 20 and not _sentences_overlap(summary, part, threshold=0.5):
                    bullets.append(part)
                    break

        return summary, bullets[:4], why
