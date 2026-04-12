"""
Load and validate configuration from config/sources.yaml and config/topics.yaml.

This module is the single entry point for all configuration. All other modules
import from here rather than reading YAML files themselves.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


# ─── Paths ───────────────────────────────────────────────────────────────────

_PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_DIR = _PROJECT_ROOT / "config"
DATA_DIR = _PROJECT_ROOT / "data"
REPORTS_DIR = _PROJECT_ROOT / "reports"

SOURCES_FILE = CONFIG_DIR / "sources.yaml"
TOPICS_FILE = CONFIG_DIR / "topics.yaml"
STATE_DB = DATA_DIR / "app_state.db"


# ─── Source config ────────────────────────────────────────────────────────────


class SourceConfig:
    """Typed wrapper around a single entry from sources.yaml."""

    def __init__(self, data: dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.enabled: bool = data.get("enabled", True)
        self.trust_weight: float = float(data.get("trust_weight", 0.5))
        self.source_type: str = data.get("source_type", "reported")
        self.homepage_url: str = data.get("homepage_url", "")
        self.section_url: str = data.get("section_url", "")
        self.feed_url: str | None = data.get("feed_url")
        self.access_method: str = data.get("access_method", "rss")
        self.requires_user_agent: bool = data.get("requires_user_agent", False)
        self.article_url_patterns: list[str] = data.get("article_url_patterns", [])
        self.listing_strategy: str = data.get("listing_strategy", "rss")
        self.parser_type: str = data.get("parser_type", "rss_standard")
        self.notes: str = data.get("notes", "")
        self.curl_examples: list[str] = data.get("curl_examples", [])

    def __repr__(self) -> str:
        return f"<SourceConfig id={self.id!r} enabled={self.enabled}>"


# ─── Topic config ─────────────────────────────────────────────────────────────


class TopicConfig:
    """Typed wrapper around a single entry from topics.yaml."""

    def __init__(self, data: dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.category: str = data.get("category", data["id"])
        self.weight: float = float(data.get("weight", 1.0))
        self.keywords: list[str] = [kw.lower() for kw in data.get("keywords", [])]

    def __repr__(self) -> str:
        return f"<TopicConfig id={self.id!r}>"


# ─── App config ───────────────────────────────────────────────────────────────


class AppConfig:
    """Loaded application configuration."""

    def __init__(self, sources: list[SourceConfig], topics: list[TopicConfig], raw: dict):
        self.sources = sources
        self.topics = topics
        self.relevance_min_keyword_matches: int = int(
            raw.get("relevance", {}).get("topic_min_keyword_matches", 1)
        )
        self.general_feed_min_matches: int = int(
            raw.get("relevance", {}).get("general_feed_min_matches", 2)
        )

    def get_source(self, source_id: str) -> SourceConfig | None:
        for s in self.sources:
            if s.id == source_id:
                return s
        return None

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


# ─── Loader ───────────────────────────────────────────────────────────────────


def load_config(
    sources_path: Path | None = None,
    topics_path: Path | None = None,
) -> AppConfig:
    """
    Load and validate sources.yaml and topics.yaml.
    Raises FileNotFoundError if either config file is missing.
    Raises ValueError on schema problems.
    """
    src_path = sources_path or SOURCES_FILE
    top_path = topics_path or TOPICS_FILE

    if not src_path.exists():
        raise FileNotFoundError(f"sources.yaml not found at {src_path}")
    if not top_path.exists():
        raise FileNotFoundError(f"topics.yaml not found at {top_path}")

    with open(src_path, encoding="utf-8") as f:
        src_raw = yaml.safe_load(f)

    with open(top_path, encoding="utf-8") as f:
        top_raw = yaml.safe_load(f)

    if not isinstance(src_raw, dict) or "sources" not in src_raw:
        raise ValueError("sources.yaml must have a top-level 'sources' list")
    if not isinstance(top_raw, dict) or "topics" not in top_raw:
        raise ValueError("topics.yaml must have a top-level 'topics' list")

    sources = []
    for entry in src_raw["sources"]:
        try:
            sources.append(SourceConfig(entry))
        except KeyError as e:
            raise ValueError(f"sources.yaml entry missing required field {e}: {entry}")

    topics = []
    for entry in top_raw["topics"]:
        try:
            topics.append(TopicConfig(entry))
        except KeyError as e:
            raise ValueError(f"topics.yaml entry missing required field {e}: {entry}")

    # Ensure data and reports dirs exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    return AppConfig(sources=sources, topics=topics, raw=top_raw)
