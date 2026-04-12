"""
Generate Markdown and JSON reports from a list of EventClusters.

Output files:
  reports/YYYY-MM-DD-ai-news-summary.md    (always written)
  reports/YYYY-MM-DD-ai-news-summary.json  (only if json=True)

Markdown structure:
  # AI News Summary
  ## Run Metadata
  ## Top Developments (medium+ by default)
  ## Honorable Mentions (low, printed if --include-low)
  ## Source Health
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import REPORTS_DIR
from .models import Article, EventCluster, Report, RunMetadata, SourceHealth


# ─── Markdown writer ──────────────────────────────────────────────────────────


def write_markdown(report: Report, path: Optional[Path] = None) -> Path:
    """Write the Markdown report. Returns the output path."""
    if path is None:
        date_str = report.generated_at.strftime("%Y-%m-%d")
        path = REPORTS_DIR / f"{date_str}-ai-news-summary.md"

    lines: list[str] = []

    # ── Title ──────────────────────────────────────────────────────────────
    lines.append("# AI News Summary\n")

    # ── Run metadata ────────────────────────────────────────────────────────
    meta = report.metadata
    lines.append("## Run Metadata\n")
    lines.append(f"- **Generated at:** {report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    if meta.window_start and meta.window_end:
        ws = meta.window_start.strftime("%Y-%m-%d %H:%M UTC")
        we = meta.window_end.strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- **Window covered:** {ws} → {we}")
    lines.append(f"- **Sources checked:** {meta.sources_checked}")
    if meta.sources_failed:
        lines.append(f"- **Sources failed:** {meta.sources_failed}")
    lines.append(f"- **Candidates found:** {meta.candidates_found}")
    lines.append(f"- **Items selected:** {meta.articles_selected}")
    lines.append("")

    # ── Top developments ────────────────────────────────────────────────────
    if report.top_items:
        lines.append("---\n")
        lines.append("## Top Developments\n")
        for cluster in report.top_items:
            _write_cluster(lines, cluster)
    else:
        lines.append("## Top Developments\n")
        lines.append("_No new noteworthy developments this run._\n")

    # ── Honorable mentions ──────────────────────────────────────────────────
    if report.honorable_mentions:
        lines.append("---\n")
        lines.append("## Honorable Mentions\n")
        for cluster in report.honorable_mentions:
            art = cluster.canonical
            date_str = _fmt_date(art.published_at)
            lines.append(
                f"- **[{art.title}]({art.url})** "
                f"— {art.source_name}, {date_str} "
                f"| `{cluster.category}` | score {cluster.score}"
            )
        lines.append("")

    # ── Source health ────────────────────────────────────────────────────────
    lines.append("---\n")
    lines.append("## Source Health\n")
    _write_source_health(lines, report.source_health)

    content = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_cluster(lines: list[str], cluster: EventCluster) -> None:
    art = cluster.canonical
    date_str = _fmt_date(art.published_at)

    # Header with link
    lines.append(f"### [{art.title}]({art.url})\n")

    # Metadata row
    label_badge = f"`{cluster.label.upper()}`"
    lines.append(
        f"**Source:** {art.source_name}  |  "
        f"**Date:** {date_str}  |  "
        f"**Score:** {cluster.score}  |  "
        f"**Label:** {label_badge}  |  "
        f"**Category:** {art.category.replace('_', ' ').title()}"
    )
    lines.append("")

    # Summary
    lines.append(art.summary)
    lines.append("")

    # Bullets
    if art.bullets:
        for bullet in art.bullets:
            lines.append(f"- {bullet}")
        lines.append("")

    # Why it matters
    if art.why_it_matters:
        lines.append(f"**Why it matters:** {art.why_it_matters}")
        lines.append("")

    # Related coverage
    related_urls = []
    for related_art in cluster.related:
        related_urls.append(
            f"[{related_art.source_name}]({related_art.url})"
        )
    if related_urls:
        lines.append(f"**Related coverage:** {' · '.join(related_urls)}")
        lines.append("")

    lines.append("---\n")


def _write_source_health(lines: list[str], health_list: list[SourceHealth]) -> None:
    ok = [h for h in health_list if h.consecutive_failures == 0 and h.last_success]
    failed = [h for h in health_list if h.consecutive_failures > 0]
    skipped = [h for h in health_list if not h.last_checked]

    if ok:
        lines.append(f"**Successful sources ({len(ok)}):** "
                     f"{', '.join(h.source_id for h in ok)}")
    if failed:
        lines.append(f"\n**Failed sources ({len(failed)}):**")
        for h in failed:
            lines.append(
                f"- `{h.source_id}`: {h.last_error or 'unknown error'} "
                f"(failures: {h.consecutive_failures})"
            )
    if skipped:
        lines.append(f"\n**Skipped sources ({len(skipped)}):** "
                     f"{', '.join(h.source_id for h in skipped)}")
    lines.append("")


# ─── JSON writer ──────────────────────────────────────────────────────────────


def write_json(report: Report, path: Optional[Path] = None) -> Path:
    """Write a structured JSON export. Returns the output path."""
    if path is None:
        date_str = report.generated_at.strftime("%Y-%m-%d")
        path = REPORTS_DIR / f"{date_str}-ai-news-summary.json"

    meta = report.metadata

    data = {
        "generated_at": report.generated_at.isoformat(),
        "metadata": {
            "run_id": meta.run_id,
            "started_at": meta.started_at.isoformat() if meta.started_at else None,
            "finished_at": meta.finished_at.isoformat() if meta.finished_at else None,
            "window_start": meta.window_start.isoformat() if meta.window_start else None,
            "window_end": meta.window_end.isoformat() if meta.window_end else None,
            "sources_checked": meta.sources_checked,
            "sources_failed": meta.sources_failed,
            "candidates_found": meta.candidates_found,
            "articles_selected": meta.articles_selected,
        },
        "top_items": [_cluster_to_dict(c) for c in report.top_items],
        "honorable_mentions": [_cluster_to_dict(c) for c in report.honorable_mentions],
        "source_health": [
            {
                "source_id": h.source_id,
                "last_checked": h.last_checked.isoformat() if h.last_checked else None,
                "last_success": h.last_success.isoformat() if h.last_success else None,
                "last_error": h.last_error,
                "consecutive_failures": h.consecutive_failures,
            }
            for h in report.source_health
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _cluster_to_dict(cluster: EventCluster) -> dict:
    art = cluster.canonical
    return {
        "cluster_id": cluster.id,
        "canonical": _article_to_dict(art),
        "score": cluster.score,
        "label": cluster.label,
        "category": cluster.category,
        "related": [
            {"source": r.source_name, "url": r.url, "title": r.title}
            for r in cluster.related
        ],
    }


def _article_to_dict(art: Article) -> dict:
    return {
        "id": art.id,
        "source_id": art.source_id,
        "source_name": art.source_name,
        "url": art.url,
        "title": art.title,
        "published_at": art.published_at.isoformat() if art.published_at else None,
        "summary": art.summary,
        "bullets": art.bullets,
        "why_it_matters": art.why_it_matters,
        "category": art.category,
        "score": art.score,
        "label": art.label,
        "topic_matches": art.topic_matches,
        "is_new": art.is_new,
    }


# ─── Terminal output ──────────────────────────────────────────────────────────


def print_terminal_summary(report: Report, top_n: Optional[int] = None) -> None:
    """Print a concise summary to stdout."""
    meta = report.metadata
    items = report.top_items[:top_n] if top_n else report.top_items

    print("\n" + "=" * 70)
    print("  AI NEWS SUMMARY")
    print("=" * 70)

    ws = meta.window_start.strftime("%Y-%m-%d") if meta.window_start else "?"
    we = meta.window_end.strftime("%Y-%m-%d") if meta.window_end else "?"
    print(f"  Window: {ws} → {we}")
    print(f"  Sources checked: {meta.sources_checked}  |  "
          f"Items selected: {meta.articles_selected}")

    if meta.sources_failed:
        print(f"  [!] {meta.sources_failed} source(s) failed")
    print()

    if not items:
        print("  No new noteworthy developments.\n")
        return

    for i, cluster in enumerate(items, 1):
        art = cluster.canonical
        date_str = _fmt_date(art.published_at)
        label_str = cluster.label.upper().ljust(8)
        print(f"  {i:2}. [{label_str}] {art.title}")
        print(f"       {art.source_name}  |  {date_str}  |  score {cluster.score}")
        print(f"       {art.url}")
        if art.summary and art.summary != art.title + ".":
            print(f"       {_truncate_line(art.summary, 80)}")
        print()

    if report.honorable_mentions:
        print(f"  + {len(report.honorable_mentions)} honorable mention(s) — "
              "use --include-low to see them\n")

    print(f"  Report: {meta.report_path or 'reports/'}")
    print("=" * 70 + "\n")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _fmt_date(dt) -> str:
    if dt is None:
        return "unknown date"
    return dt.strftime("%Y-%m-%d")


def _truncate_line(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."
