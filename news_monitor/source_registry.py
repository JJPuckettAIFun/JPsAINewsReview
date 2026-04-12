"""
Source registry: orchestrates fetching + parsing for each enabled source.

This is the main entry point for data collection. It reads from sources.yaml
(via AppConfig), fetches each source using the correct fetcher/parser combo,
and returns (articles, health_updates) to the pipeline.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import AppConfig, SourceConfig
from .fetchers import RSSFetcher, HTMLFetcher
from .models import RawArticle, SourceHealth
from .parsers import get_parser, RSSStandardParser, AnthropicParser
from .utils import utcnow

logger = logging.getLogger(__name__)


# ─── Fetch result ─────────────────────────────────────────────────────────────


class FetchResult:
    def __init__(
        self,
        source_id: str,
        articles: list[RawArticle],
        health: SourceHealth,
        error: Optional[str] = None,
    ):
        self.source_id = source_id
        self.articles = articles
        self.health = health
        self.error = error

    @property
    def ok(self) -> bool:
        return self.error is None


# ─── Source registry ──────────────────────────────────────────────────────────


class SourceRegistry:
    """
    For each enabled source, fetches and parses articles.
    Returns a list of FetchResult objects.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._rss_fetcher = RSSFetcher()
        self._html_fetcher = HTMLFetcher()

    def fetch_all(
        self,
        source_ids: Optional[list[str]] = None,
    ) -> list[FetchResult]:
        """
        Fetch all enabled sources (or a subset if source_ids is provided).
        Returns one FetchResult per source; never raises.
        """
        sources = self.config.enabled_sources()
        if source_ids:
            sources = [s for s in sources if s.id in source_ids]

        results: list[FetchResult] = []
        for source in sources:
            logger.info("Fetching source: %s (%s)", source.id, source.name)
            result = self._fetch_one(source)
            results.append(result)
            if result.ok:
                logger.info(
                    "  -> %d articles from %s", len(result.articles), source.id
                )
            else:
                logger.warning("  -> FAILED %s: %s", source.id, result.error)

        return results

    def _fetch_one(self, source: SourceConfig) -> FetchResult:
        health = SourceHealth(
            source_id=source.id,
            last_checked=utcnow(),
        )
        try:
            articles, error = self._dispatch(source)
            if error:
                health.last_error = error
                health.consecutive_failures = 1
                return FetchResult(source.id, [], health, error)

            health.last_success = utcnow()
            health.consecutive_failures = 0
            health.total_articles_fetched = len(articles)
            return FetchResult(source.id, articles, health)

        except Exception as exc:
            error = f"unexpected error: {exc}"
            logger.exception("Unexpected error fetching %s", source.id)
            health.last_error = error
            health.consecutive_failures = 1
            return FetchResult(source.id, [], health, error)

    def _dispatch(
        self, source: SourceConfig
    ) -> tuple[list[RawArticle], Optional[str]]:
        """Route source to the correct fetcher+parser combo."""
        access = source.access_method

        # ── RSS-based sources ────────────────────────────────────────────────
        if access in ("rss", "rss_with_ua"):
            if not source.feed_url:
                return [], f"source {source.id} has access_method=rss but no feed_url"

            requires_ua = source.requires_user_agent or access == "rss_with_ua"
            parsed, error = self._rss_fetcher.fetch(
                source.feed_url,
                requires_ua=requires_ua,
            )
            if error:
                return [], error

            parser = get_parser(source.parser_type)
            if not isinstance(parser, RSSStandardParser):
                return [], f"source {source.id}: rss access_method requires rss_standard parser"

            articles = parser.parse(parsed, source.id, source.name)
            return articles, None

        # ── HTML / Next.js sources ────────────────────────────────────────────
        elif access in ("html", "html_nextjs"):
            url = source.section_url or source.homepage_url
            html, error = self._html_fetcher.fetch(
                url,
                requires_ua=source.requires_user_agent,
            )
            if error:
                return [], error

            parser = get_parser(source.parser_type)
            if isinstance(parser, AnthropicParser):
                articles = parser.parse(html, source.id, source.name, url)
            else:
                return [], f"source {source.id}: unsupported parser {source.parser_type} for html access"
            return articles, None

        else:
            return [], f"unknown access_method: {access!r}"
