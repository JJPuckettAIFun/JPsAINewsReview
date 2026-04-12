"""
Source-specific parsers that convert raw feed/HTML data into RawArticle lists.

Parser classes:
  RSSStandardParser  - handles any well-formed RSS/Atom feed (most sources)
  AnthropicParser    - parses Next.js SSR HTML from anthropic.com

Each parser returns a list[RawArticle] and is selected by sources.yaml parser_type.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from bs4 import BeautifulSoup
import feedparser

from .models import RawArticle
from .utils import struct_time_to_datetime, parse_date, truncate, BROWSER_UA


# ─── RSS standard parser ──────────────────────────────────────────────────────


class RSSStandardParser:
    """
    Convert a feedparser.FeedParserDict into RawArticle objects.

    Works for: OpenAI, DeepMind, HuggingFace, TechCrunch, ArsTechnica,
               Semafor, WIRED, and any other standard RSS 2.0 / Atom feed.
    """

    def parse(
        self,
        parsed: feedparser.FeedParserDict,
        source_id: str,
        source_name: str,
    ) -> list[RawArticle]:
        articles = []
        for entry in parsed.get("entries", []):
            url = entry.get("link", "").strip()
            if not url:
                continue

            title = entry.get("title", "").strip()
            if not title:
                continue

            # Parse publication date
            published_at: Optional[datetime] = None
            if entry.get("published_parsed"):
                published_at = struct_time_to_datetime(entry["published_parsed"])
            elif entry.get("updated_parsed"):
                published_at = struct_time_to_datetime(entry["updated_parsed"])
            elif entry.get("published"):
                published_at = parse_date(entry["published"])

            # Extract summary/description - strip HTML tags
            summary_raw = (
                entry.get("summary", "")
                or entry.get("description", "")
                or ""
            )
            summary = _strip_html(summary_raw)

            # Tags
            tags = [t.get("term", "") for t in entry.get("tags", []) if t.get("term")]

            articles.append(RawArticle(
                source_id=source_id,
                source_name=source_name,
                url=url,
                title=title,
                published_at=published_at,
                summary=truncate(summary, 600),
                author=_get_author(entry),
                tags=tags,
            ))

        return articles


# ─── Anthropic Next.js parser ─────────────────────────────────────────────────


class AnthropicParser:
    """
    Parse Anthropic's news/research pages (Next.js SSR).

    Strategy:
    1. Look for <script id="__NEXT_DATA__"> and parse its JSON.
       Recursively search for dicts that look like article entries
       (have a url/slug/href and a title and optionally a publishedAt/date).
    2. Fall back to scanning <a href> links that match /news/* or /research/*
       patterns and extracting title text.

    The __NEXT_DATA__ blob changes layout across deployments; the recursive
    search is intentionally loose to stay robust.
    """

    BASE_URL = "https://www.anthropic.com"

    def parse(
        self,
        html: str,
        source_id: str,
        source_name: str,
        section_url: str = "",
    ) -> list[RawArticle]:
        soup = BeautifulSoup(html, "html.parser")

        # Try primary strategy: __NEXT_DATA__ JSON
        articles = self._from_next_data(soup, source_id, source_name, section_url)
        if articles:
            return articles

        # Fallback: link scanning
        return self._from_link_scan(soup, source_id, source_name, section_url)

    # ── Primary: __NEXT_DATA__ ────────────────────────────────────────────────

    def _from_next_data(
        self,
        soup: BeautifulSoup,
        source_id: str,
        source_name: str,
        section_url: str,
    ) -> list[RawArticle]:
        script_tag = soup.find("script", id="__NEXT_DATA__")
        if not script_tag:
            return []

        try:
            data = json.loads(script_tag.string or "")
        except (json.JSONDecodeError, TypeError):
            return []

        candidates: list[dict] = []
        _find_article_dicts(data, candidates)

        articles = []
        seen_urls: set[str] = set()
        for candidate in candidates:
            url = self._extract_url(candidate, section_url)
            if not url or url in seen_urls:
                continue
            title = self._extract_title(candidate)
            if not title:
                continue
            published_at = self._extract_date(candidate)
            summary = self._extract_summary(candidate)
            seen_urls.add(url)
            articles.append(RawArticle(
                source_id=source_id,
                source_name=source_name,
                url=url,
                title=title,
                published_at=published_at,
                summary=summary,
            ))

        return articles

    def _extract_url(self, d: dict, section_url: str) -> Optional[str]:
        # Try various field names
        for key in ("href", "url", "slug", "path", "link"):
            val = d.get(key, "")
            if isinstance(val, str) and val:
                if val.startswith("http"):
                    return val
                if val.startswith("/"):
                    return self.BASE_URL + val
        return None

    def _extract_title(self, d: dict) -> Optional[str]:
        for key in ("title", "headline", "name", "heading"):
            val = d.get(key, "")
            if isinstance(val, str) and len(val) > 5:
                return val.strip()
        return None

    def _extract_date(self, d: dict) -> Optional[datetime]:
        for key in ("publishedAt", "published_at", "date", "createdAt", "updatedAt"):
            val = d.get(key, "")
            if isinstance(val, str) and val:
                dt = parse_date(val)
                if dt:
                    return dt
        return None

    def _extract_summary(self, d: dict) -> Optional[str]:
        for key in ("description", "summary", "excerpt", "body", "content"):
            val = d.get(key, "")
            if isinstance(val, str) and len(val) > 20:
                return truncate(_strip_html(val), 600)
        return None

    # ── Fallback: link scan ───────────────────────────────────────────────────

    def _from_link_scan(
        self,
        soup: BeautifulSoup,
        source_id: str,
        source_name: str,
        section_url: str,
    ) -> list[RawArticle]:
        """
        Scan for <a> tags matching Anthropic article URL patterns.
        Less reliable (no dates), used only when __NEXT_DATA__ fails.
        """
        is_research = "research" in section_url
        pattern = re.compile(
            r"^(/research/[^/\s]+|/news/[^/\s]+|/[a-z][a-z0-9\-]+)$"
        )

        articles = []
        seen: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not pattern.match(href):
                continue
            # Skip generic navigation links
            if href in ("/news", "/research", "/"):
                continue

            url = self.BASE_URL + href
            if url in seen:
                continue

            title = a_tag.get_text(strip=True)
            if not title or len(title) < 10:
                # Try parent element for title
                parent = a_tag.find_parent(["article", "div", "li", "section"])
                if parent:
                    heading = parent.find(["h1", "h2", "h3"])
                    if heading:
                        title = heading.get_text(strip=True)
            if not title or len(title) < 10:
                continue

            seen.add(url)
            articles.append(RawArticle(
                source_id=source_id,
                source_name=source_name,
                url=url,
                title=title,
                published_at=None,
                summary=None,
            ))

        return articles


# ─── Parser registry ─────────────────────────────────────────────────────────


_RSS_PARSER = RSSStandardParser()
_ANTHROPIC_PARSER = AnthropicParser()

PARSER_MAP = {
    "rss_standard": _RSS_PARSER,
    "anthropic": _ANTHROPIC_PARSER,
}


def get_parser(parser_type: str) -> RSSStandardParser | AnthropicParser:
    parser = PARSER_MAP.get(parser_type)
    if parser is None:
        raise ValueError(
            f"Unknown parser_type: {parser_type!r}. "
            f"Valid options: {list(PARSER_MAP)}"
        )
    return parser


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode entities from a string."""
    if not text:
        return ""
    try:
        soup = BeautifulSoup(text, "html.parser")
        return soup.get_text(separator=" ").strip()
    except Exception:
        # Fallback: simple regex
        return re.sub(r"<[^>]+>", " ", text).strip()


def _get_author(entry: dict) -> Optional[str]:
    if entry.get("author"):
        return entry["author"]
    authors = entry.get("authors", [])
    if authors and authors[0].get("name"):
        return authors[0]["name"]
    return None


def _find_article_dicts(obj: object, results: list[dict], depth: int = 0) -> None:
    """
    Recursively walk a JSON structure looking for dicts that resemble
    article metadata (have a title/headline and a url/href/slug).
    Limit depth to avoid infinite recursion on circular-ish structures.
    """
    if depth > 20:
        return
    if isinstance(obj, dict):
        has_title = any(k in obj for k in ("title", "headline", "name"))
        has_url = any(k in obj for k in ("href", "url", "slug", "path", "link"))
        if has_title and has_url:
            results.append(obj)
        for v in obj.values():
            _find_article_dicts(v, results, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            _find_article_dicts(item, results, depth + 1)
