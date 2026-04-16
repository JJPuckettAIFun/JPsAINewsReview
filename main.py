"""
AI News Monitor - Main entry point.

Usage:
  python main.py                          # default run (auto lookback window)
  python main.py --since 14d             # override lookback to 14 days
  python main.py --top 10                # show only top 10 items
  python main.py --include-low           # include low-priority items
  python main.py --category research     # filter to a specific category
  python main.py --source openai_news    # only run specific source(s)
  python main.py --markdown              # write Markdown report (default: always)
  python main.py --json                  # also write JSON export
  python main.py --force-full-refresh    # clear state and re-process last 14 days
  python main.py --quiet                 # suppress terminal output (only write report)
  python main.py --list-sources          # print all configured sources and exit
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from news_monitor.config import load_config, DATA_DIR
from news_monitor.models import (
    Article, EventCluster, Report, RunMetadata, SourceHealth,
)
from news_monitor.storage import (
    init_db, begin_run, finish_run,
    get_last_successful_run, mark_articles_seen,
    get_seen_fingerprints, get_seen_title_fingerprints,
    update_source_health, get_all_source_health,
    get_known_source_ids,
    upsert_cluster, clear_seen_state,
    STATE_DB,
)
from news_monitor.source_registry import SourceRegistry
from news_monitor.normalizers import make_normalized_fields, make_fingerprints
from news_monitor.dedupe import filter_seen, cluster_articles
from news_monitor.classifier import Classifier
from news_monitor.ranker import Ranker, score_to_label
from news_monitor.summarizer import Summarizer
from news_monitor.reporter import write_markdown, write_json, print_terminal_summary
from news_monitor.utils import utcnow, days_ago, parse_since_arg
from news_monitor.fetchers import ArticleContentFetcher
from news_monitor.content_extractor import extract_article_text


# ─── Logging ─────────────────────────────────────────────────────────────────

_LOG_FILE = Path(__file__).parent / "data" / "last_run.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, mode="w", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ─── Constants ───────────────────────────────────────────────────────────────

DEFAULT_LOOKBACK_DAYS = 14
GENERAL_FEED_SOURCE_IDS = {"semafor", "hacker_news"}    # sources needing stricter topic filtering


# ─── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="AI News Monitor — fetch, rank, and summarize AI news.",
    )
    p.add_argument(
        "--since",
        metavar="DURATION",
        help="Lookback window override, e.g. '14d', '48h', '7d'. "
             "Overrides last-run auto-detection.",
    )
    p.add_argument(
        "--top",
        type=int,
        default=None,
        metavar="N",
        help="Limit terminal output to top N items (default: all).",
    )
    p.add_argument(
        "--include-low",
        action="store_true",
        help="Include low-priority items in the report.",
    )
    p.add_argument(
        "--category",
        metavar="CATEGORY",
        help=(
            "Filter output to a single category: "
            "model_releases | product_launches | agents_tooling | research | "
            "infrastructure | policy_regulation | safety_evals | "
            "funding_ma | enterprise_adoption | other"
        ),
    )
    p.add_argument(
        "--source",
        metavar="SOURCE_IDS",
        help="Comma-separated source IDs to run (e.g. openai_news,anthropic_news).",
    )
    p.add_argument(
        "--markdown",
        action="store_true",
        default=True,
        help="Write Markdown report to reports/ (default: on).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Also write a JSON export alongside the Markdown report.",
    )
    p.add_argument(
        "--force-full-refresh",
        action="store_true",
        help="Clear all seen state and re-process the last 14 days.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress terminal output; only write report files.",
    )
    p.add_argument(
        "--list-sources",
        action="store_true",
        help="Print all configured sources and exit.",
    )
    p.add_argument(
        "--min-score",
        type=int,
        default=None,
        metavar="N",
        help="Override minimum score threshold for inclusion (default: 50).",
    )
    return p


# ─── Pipeline ────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    """
    Main pipeline. Returns exit code (0=success, 1=error).
    """
    # ── Load config ──────────────────────────────────────────────────────────
    try:
        config = load_config()
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] Config load failed: {exc}", file=sys.stderr)
        return 1

    if args.list_sources:
        _print_sources(config)
        return 0

    # ── Initialize DB ────────────────────────────────────────────────────────
    init_db()

    # ── Force refresh ────────────────────────────────────────────────────────
    if args.force_full_refresh:
        logger.info("Force full refresh: clearing seen state")
        clear_seen_state()

    # ── Determine time window ────────────────────────────────────────────────
    now = utcnow()

    if args.since:
        window_start = parse_since_arg(args.since)
        if window_start is None:
            print(
                f"[ERROR] Invalid --since value: {args.since!r}. "
                "Use format like '14d', '48h'.",
                file=sys.stderr,
            )
            return 1
        is_first_run = False
    else:
        last_run = get_last_successful_run()
        if last_run is None:
            # First run: use default lookback
            window_start = days_ago(DEFAULT_LOOKBACK_DAYS)
            is_first_run = True
            logger.info("First run detected — using %d-day lookback", DEFAULT_LOOKBACK_DAYS)
        else:
            window_start = last_run
            is_first_run = False
            logger.info(
                "Resuming from last successful run at %s",
                last_run.strftime("%Y-%m-%d %H:%M UTC"),
            )

    window_end = now

    # ── Start run record ─────────────────────────────────────────────────────
    run_id = begin_run(window_start, window_end)
    meta = RunMetadata(
        run_id=run_id,
        started_at=now,
        window_start=window_start,
        window_end=window_end,
    )

    try:
        result = _execute_pipeline(args, config, meta, run_id, window_start, window_end)
    except KeyboardInterrupt:
        meta.status = "failed"
        meta.finished_at = utcnow()
        finish_run(run_id, meta)
        print("\n[Interrupted]", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.exception("Pipeline failed unexpectedly")
        meta.status = "failed"
        meta.finished_at = utcnow()
        finish_run(run_id, meta)
        print(f"[ERROR] Unexpected failure: {exc}", file=sys.stderr)
        return 1

    return result


def _execute_pipeline(
    args: argparse.Namespace,
    config,
    meta: RunMetadata,
    run_id: str,
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Inner pipeline — all exceptions bubble up to run()."""

    # ── Source selection ──────────────────────────────────────────────────────
    source_ids: Optional[list[str]] = None
    if args.source:
        source_ids = [s.strip() for s in args.source.split(",")]
        # Validate
        valid_ids = {s.id for s in config.enabled_sources()}
        bad = [sid for sid in source_ids if sid not in valid_ids]
        if bad:
            print(
                f"[ERROR] Unknown source IDs: {bad}. "
                f"Valid: {sorted(valid_ids)}",
                file=sys.stderr,
            )
            return 1

    # ── Snapshot which sources have prior history (before this run updates health) ─
    # Used below to give brand-new sources a full 14-day lookback window.
    known_source_ids = get_known_source_ids()

    # ── Fetch all sources ─────────────────────────────────────────────────────
    registry = SourceRegistry(config)
    fetch_results = registry.fetch_all(source_ids=source_ids)

    meta.sources_checked = len(fetch_results)
    meta.sources_failed = sum(1 for r in fetch_results if not r.ok)
    _id_to_name = {s.id: s.name for s in config.sources}
    meta.checked_source_names = [_id_to_name.get(r.health.source_id, r.health.source_id) for r in fetch_results]

    # Update source health in DB
    for result in fetch_results:
        update_source_health(result.health)

    # ── Collect all raw articles ──────────────────────────────────────────────
    all_raw = []
    for result in fetch_results:
        all_raw.extend(result.articles)

    logger.info("Total raw articles collected: %d", len(all_raw))

    # ── Date filtering ────────────────────────────────────────────────────────
    # Per-source floors:
    #   - Existing sources: window_start - 48h buffer (normal cadence)
    #   - New sources (no prior successful fetch): 14-day lookback so their
    #     first run surfaces a useful backfill of recent content.
    # The seen-URL dedup layer is the real gatekeeper for "already reported".
    from datetime import timedelta
    default_floor = window_start - timedelta(hours=48)
    new_source_floor = days_ago(DEFAULT_LOOKBACK_DAYS)

    # Log any sources getting the extended window
    new_sources_this_run = [
        r.health.source_id for r in fetch_results
        if r.health.source_id not in known_source_ids
    ]
    if new_sources_this_run:
        logger.info(
            "New sources detected — applying 14-day lookback: %s",
            ", ".join(new_sources_this_run),
        )

    date_filtered = []
    for article in all_raw:
        floor = new_source_floor if article.source_id not in known_source_ids else default_floor
        if article.published_at is None:
            # No date — keep it; dedup will catch it if seen before
            date_filtered.append(article)
        elif article.published_at >= floor:
            date_filtered.append(article)

    logger.info("After date filter: %d articles", len(date_filtered))

    # ── Load seen fingerprints ────────────────────────────────────────────────
    seen_url_fps = get_seen_fingerprints()
    seen_title_fps = get_seen_title_fingerprints()

    # ── Deduplicate against history ───────────────────────────────────────────
    new_raw, previously_seen_raw = filter_seen(
        date_filtered, seen_url_fps, seen_title_fps
    )
    logger.info(
        "New articles: %d | Previously seen: %d",
        len(new_raw), len(previously_seen_raw),
    )

    meta.candidates_found = len(new_raw)

    if not new_raw:
        logger.info("No new articles found this run.")
        meta.status = "success"
        meta.finished_at = utcnow()
        meta.articles_selected = 0

        # Write report BEFORE finish_run so report_path is saved to the DB
        report = _build_report(meta, [], [], config)
        report_path = write_markdown(report)
        meta.report_path = str(report_path)
        finish_run(run_id, meta)

        if args.json:
            write_json(report)
        if not args.quiet:
            print_terminal_summary(report, top_n=args.top)
        return 0

    # ── Initialize processing components ──────────────────────────────────────
    classifier = Classifier(config)
    ranker = Ranker(config)
    summarizer = Summarizer()

    # ── Fetch full article content (parallel, best-effort) ───────────────────
    # Runs after dedup so we only fetch articles that are genuinely new.
    # Uses a thread pool so the N fetches happen concurrently rather than
    # sequentially — typical wall-clock cost is ~5-10 s for 20-30 articles.
    full_text_cache: dict[str, Optional[str]] = {}
    _content_fetcher = ArticleContentFetcher()
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed as _as_completed

        def _fetch_one_article(raw_article):
            html, err = _content_fetcher.fetch(raw_article.url)
            if html:
                return raw_article.url, extract_article_text(html)
            return raw_article.url, None

        logger.info("Fetching full article content for %d new articles…", len(new_raw))
        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {pool.submit(_fetch_one_article, r): r for r in new_raw}
            for fut in _as_completed(futs):
                url, text = fut.result()
                full_text_cache[url] = text

        fetched_count = sum(1 for v in full_text_cache.values() if v)
        logger.info("Full article text retrieved: %d / %d", fetched_count, len(new_raw))
    except Exception as exc:
        logger.warning("Article content fetch failed: %s — falling back to feed text", exc)
    finally:
        _content_fetcher.close()

    # ── Build source coverage map (for multi-source scoring) ─────────────────
    # Group by normalized title to find cross-source coverage
    title_to_sources: dict[str, set[str]] = {}
    for article in new_raw:
        from news_monitor.utils import normalize_title
        key = normalize_title(article.title)
        title_to_sources.setdefault(key, set()).add(article.source_id)

    # ── Convert RawArticles to Articles ──────────────────────────────────────
    articles: list[Article] = []
    source_cfg_cache = {s.id: s for s in config.sources}

    for raw in new_raw:
        url_norm, title_norm = make_normalized_fields(raw.url, raw.title)
        url_fp, title_fp = make_fingerprints(raw.url, raw.title)

        source_cfg = source_cfg_cache.get(raw.source_id)
        if not source_cfg:
            continue

        # ── AI relevance filter (general feeds only) ─────────────────────────
        if raw.source_id in GENERAL_FEED_SOURCE_IDS:
            if not classifier.is_ai_relevant(
                raw.title,
                raw.summary or "",
                raw.tags,
                min_matches=config.general_feed_min_matches,
            ):
                continue

        # ── Classify ─────────────────────────────────────────────────────────
        category, topic_matches = classifier.classify(
            raw.title, raw.summary or "", raw.tags
        )

        # ── Score ─────────────────────────────────────────────────────────────
        from news_monitor.utils import normalize_title
        num_covering = len(title_to_sources.get(normalize_title(raw.title), {1}))

        score, label = ranker.score(
            raw,
            source_cfg,
            topic_matches,
            is_new=True,
            num_covering_sources=num_covering,
        )

        # ── Summarize ─────────────────────────────────────────────────────────
        summary, bullets, why = summarizer.summarize(
            raw.title,
            raw.summary,
            category,
            source_cfg.source_type,
            topic_matches,
            full_text=full_text_cache.get(raw.url),
        )

        article = Article(
            id=Article.make_id(url_norm),
            source_id=raw.source_id,
            source_name=raw.source_name,
            source_type=source_cfg.source_type,
            trust_weight=source_cfg.trust_weight,
            url=raw.url,
            url_normalized=url_norm,
            title=raw.title,
            title_normalized=title_norm,
            published_at=raw.published_at,
            summary=summary,
            bullets=bullets,
            why_it_matters=why,
            category=category,
            topic_matches=topic_matches,
            score=score,
            label=label,
            is_new=True,
        )
        articles.append(article)

    logger.info("Processed articles (after topic filter): %d", len(articles))

    # ── Cluster related coverage ──────────────────────────────────────────────
    clusters = cluster_articles(articles)
    logger.info("Event clusters formed: %d", len(clusters))

    # ── Persist clusters to DB ────────────────────────────────────────────────
    for cluster in clusters:
        upsert_cluster(
            cluster_id=cluster.id,
            canonical_url=cluster.canonical.url,
            canonical_title=cluster.canonical.title,
            category=cluster.category,
            member_urls=[a.url for a in cluster.all_articles],
        )

    # ── Mark articles as seen ─────────────────────────────────────────────────
    mark_articles_seen(articles, run_id)

    # ── Apply CLI filters ─────────────────────────────────────────────────────
    min_score = args.min_score if args.min_score is not None else 50
    category_filter = args.category

    top_clusters: list[EventCluster] = []
    honorable: list[EventCluster] = []

    for cluster in clusters:
        if category_filter and cluster.category != category_filter:
            continue
        if cluster.score >= min_score:
            top_clusters.append(cluster)
        elif args.include_low or cluster.label != "low":
            honorable.append(cluster)

    # ── Finalize metadata ─────────────────────────────────────────────────────
    meta.articles_selected = len(top_clusters)
    meta.status = "success"
    meta.finished_at = utcnow()

    # ── Build and write report BEFORE finish_run so report_path is saved ─────
    source_health = get_all_source_health()
    report = _build_report(meta, top_clusters, honorable, config, source_health)
    report_path = write_markdown(report)
    meta.report_path = str(report_path)

    # ── Write state ───────────────────────────────────────────────────────────
    finish_run(run_id, meta)

    if args.json:
        write_json(report)

    if not args.quiet:
        print_terminal_summary(report, top_n=args.top)

    logger.info("Run complete. Report: %s", report_path)
    return 0


def _build_report(
    meta: RunMetadata,
    top_items: list[EventCluster],
    honorable: list[EventCluster],
    config,
    source_health: Optional[list[SourceHealth]] = None,
) -> Report:
    return Report(
        metadata=meta,
        top_items=top_items,
        honorable_mentions=honorable,
        source_health=source_health or [],
        generated_at=utcnow(),
    )


def _print_sources(config) -> None:
    print(f"\n{'ID':<25} {'Name':<35} {'Enabled':<8} {'Type':<10} {'Method'}")
    print("-" * 90)
    for s in config.sources:
        enabled = "yes" if s.enabled else "NO"
        print(
            f"{s.id:<25} {s.name:<35} {enabled:<8} "
            f"{s.source_type:<10} {s.access_method}"
        )
    print()


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(run(args))


if __name__ == "__main__":
    main()
