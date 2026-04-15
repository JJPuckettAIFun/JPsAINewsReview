"""
Extract the main body text from an article HTML page.

Uses a priority list of common article container selectors, then falls back
to collecting all <p> tags from <main> or <body>.  Non-content regions
(nav, ads, sidebars, footers, share bars) are stripped first.

Returns plain text suitable for the summarizer.
"""

from __future__ import annotations

import re
from typing import Optional

try:
    from bs4 import BeautifulSoup, Tag
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False


# ─── Elements / classes to strip before extraction ───────────────────────────

_STRIP_TAGS = frozenset({
    "nav", "header", "footer", "aside", "script", "style",
    "noscript", "form", "iframe", "figure", "figcaption",
    "button", "svg", "picture",
})

_SKIP_CLASS_RE = re.compile(
    r"\b(nav|menu|sidebar|header|footer|breadcrumb|ad[s_-]|advertisement|"
    r"promo|banner|related|recommended|share|social|comment|subscribe|"
    r"newsletter|cookie|popup|modal|widget|masthead|byline|dateline|"
    r"tag|label|caption|credit|author-bio|read-more|cta)\b",
    re.IGNORECASE,
)

# ─── Article container selectors (tried in priority order) ───────────────────

_ARTICLE_SELECTORS = [
    "[itemprop='articleBody']",
    "article",
    ".article-body",
    ".article__body",
    ".article__content",
    ".entry-content",
    ".post-content",
    ".post-body",
    ".story-body",
    ".article-content",
    ".content-body",
    ".body-copy",
    ".prose",
    ".rich-text",
    ".blog-content",
    "main",
]

# Sentences that are navigation / boilerplate noise
_NOISE_RE = re.compile(
    r"^(subscribe|sign up|read more|click here|follow us|share this|"
    r"related:|see also:|updated:|published:|©|\d+ min read)",
    re.IGNORECASE,
)


# ─── Public API ───────────────────────────────────────────────────────────────


def extract_article_text(html: str, max_chars: int = 5000) -> Optional[str]:
    """
    Given raw HTML, return the article body as clean plain text.
    Returns None if extraction fails or yields < 150 chars.

    max_chars:  Hard cap on returned text length.  5000 chars is enough for
                4-6 paragraphs — plenty for rich summaries without parsing
                the entire article.
    """
    if not _BS4_AVAILABLE or not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content structural elements
        for tag in soup.find_all(_STRIP_TAGS):
            tag.decompose()

        # Find the best content container
        container = None
        for selector in _ARTICLE_SELECTORS:
            found = soup.select_one(selector)
            if found and _text_length(found) > 200:
                container = found
                break

        if container is None:
            container = soup.find("body") or soup

        # Collect clean paragraphs
        paragraphs: list[str] = []
        for p in container.find_all("p"):
            if _in_skip_element(p):
                continue
            text = _clean_text(p.get_text(separator=" "))
            if len(text) < 40:
                continue
            if _NOISE_RE.match(text):
                continue
            paragraphs.append(text)

        if not paragraphs:
            return None

        full_text = "\n\n".join(paragraphs)
        if len(full_text) < 150:
            return None

        return full_text[:max_chars]

    except Exception:
        return None


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _text_length(tag: "Tag") -> int:
    return len(tag.get_text(strip=True))


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _in_skip_element(tag: "Tag") -> bool:
    """Return True if the tag or any ancestor matches skip-class patterns."""
    for el in [tag] + list(tag.parents):
        classes = " ".join(el.get("class") or []) if hasattr(el, "get") else ""
        if classes and _SKIP_CLASS_RE.search(classes):
            return True
        role = el.get("role", "") if hasattr(el, "get") else ""
        if role in ("navigation", "banner", "complementary", "contentinfo"):
            return True
    return False
