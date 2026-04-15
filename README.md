# JPsAINewsReview

A local Python CLI tool that monitors trusted AI news sources and reports only new, noteworthy developments since the last successful run.

---

## Changelog

### v1.3.0 — 2026-04-15

**Manage Sources UI**
- Added a full **Source Manager** panel accessible directly from the sidebar, just below the Run Now button.
- Displays all configured sources in a table showing name, type, trust weight, live status (OK / failed), article count from the last run, and a toggle to enable/disable each source.
- Sources that failed their last fetch are highlighted in red with the exact error message, failure count, and last successful fetch date — no more guessing why a source went silent.
- A red banner appears at the top of the panel when any source is currently failing.
- **Add Source:** click "Add Source" to open a modal and fill in name, feed URL, homepage URL, source type, trust weight, and whether a browser user-agent is required. The new source is written directly to `config/sources.yaml` and takes effect on the next run.
- **Delete Source:** each row has a delete button (pinned top-right) to permanently remove a source from `sources.yaml`.
- **Toggle Source:** enable or disable any source with a single click without deleting it.

**Rich Article Summaries**
- The pipeline now fetches the full HTML of each new article in parallel (8 threads) after deduplication.
- A new `content_extractor.py` module strips navigation, headers, footers, and ads using CSS selector priority matching, returning up to 5,000 characters of clean article body text.
- The summarizer uses this full text — when available — to build richer, multi-paragraph summaries with more specific bullet points instead of the short RSS-snippet fallback.
- Falls back gracefully to the RSS description when full-page fetch fails or is blocked.

**Collapsible Report Sections**
- Article entries in the report viewer are now collapsible — click any article header to expand or collapse it. Articles start expanded.
- The **Run Metadata** section is collapsible and starts collapsed to keep the top of the report clean.

**Run Overlay — "Did You Know?" Tips**
- The run-in-progress overlay now shows a **"Did You Know?"** tip box pinned at the top of the overlay, cycling through facts about how the pipeline works while you wait.
- The overlay also shows the current source count and an estimated time range so the wait feels less like a black box.
- On failure, the overlay stays visible (no longer auto-dismisses), shows the full log output from `data/last_run.log`, and offers **Back to Reports** and **Try Again** buttons.

**Persistent Error Logging**
- Every run now writes a full log to `data/last_run.log` (UTF-8, overwritten each run).
- The web UI fetches this log on failure so you can read the exact error without needing a terminal open.

**UI Redesign**
- Color scheme updated to blues, greens, and greys: sidebar background `#1C3250`, accent `#2A9C72`, link color `#2F7EC0`, content background `#F2F6FA`.
- Masthead is larger and more prominent (22px, bold, accent-colored bottom border).
- Article report area expanded to 1080px max-width to make better use of wide screens.
- Sources checked in Run Metadata now lists source names, not just a count.

**Feed Fixes**
- Fixed The Rundown AI feed URL (switched to beehiiv CDN: `rss.beehiiv.com/feeds/2R3C6Bt5wj.xml`).
- Fixed TLDR AI feed URL (`tldr.tech/api/rss/ai`).
- Disabled Meta AI Blog (no public RSS feed exists).
- Added XML sanitizer in `fetchers.py` to handle malformed RSS feeds with illegal control characters or bare ampersands — falls back to raw-fetch + sanitize + re-parse when feedparser enters bozo mode.
- `RSSFetcher` now always sends a browser User-Agent to avoid 403s on UA-sensitive feeds.

**Bug Fixes**
- Fixed recurring `UnicodeEncodeError` crash on Windows (cp1252 terminals) — all terminal output now goes through `_safe_print()` which replaces unencodable characters rather than crashing.

---

### v1.2.0 — 2026-04-14
**Delete Run**
- Added a trash icon button to each report in the sidebar of the web UI.
- Clicking it deletes the report file **and** removes its run record and seen-article history from the database, so those articles will surface again on the next run.
- Useful for re-running after a bug fix or when a run returned 0 results unexpectedly.

**Date filter loosened**
- Added a 48-hour buffer before `window_start` so articles with slightly stale or batched timestamps are no longer incorrectly dropped.
- The seen-URL dedup layer remains the primary mechanism for avoiding re-reporting — the date filter is now a loose safety net rather than a hard cutoff.

**Summarizer fix — no more duplicate summary/bullet**
- The summary paragraph now draws up to 3–4 sentences from the feed description.
- Bullet points are built exclusively from sentences **not** already used in the summary, with an overlap check to prevent near-identical phrasing from sneaking through.
- Falls back gracefully to title sub-phrases (de-duplicated against the summary) when the feed description is too short.

**Windows terminal fix**
- Replaced the `→` arrow in terminal output with `->` to prevent a `UnicodeEncodeError` on Windows systems using the `cp1252` codec.

---

## Purpose

When you run this tool, you get a concise ranked summary of important new AI developments across trusted sources — model releases, research, policy, safety, infrastructure, agents, and more — with links, summaries, bullet points, and "why it matters" context for each item.

---

## Architecture

```
main.py                      CLI entry point and pipeline orchestrator
news_monitor/
  config.py                  Load config/sources.yaml + topics.yaml
  source_registry.py         Orchestrate fetch + parse for each source
  fetchers.py                RSSFetcher (feedparser) + HTMLFetcher (requests)
  parsers.py                 RSSStandardParser + AnthropicParser (Next.js)
  normalizers.py             URL and title normalization
  dedupe.py                  4-layer dedup + event clustering
  ranker.py                  Scoring model (0-100) + labels
  classifier.py              Keyword-based category + topic matching
  summarizer.py              Extractive summary, bullets, why-it-matters
  content_extractor.py       Full-page HTML article text extraction
  storage.py                 SQLite state persistence
  reporter.py                Markdown + JSON report generation
  models.py                  Dataclasses: RawArticle, Article, EventCluster, etc.
  utils.py                   URL norm, date parsing, helpers

config/
  sources.yaml               Canonical source registry (see below)
  topics.yaml                Topics + keywords for classification + scoring

data/
  app_state.db               SQLite state database
  last_run.log               Full log of the most recent run (UTF-8, overwritten each run)

reports/
  YYYY-MM-DD-ai-news-summary.md
  YYYY-MM-DD-ai-news-summary.json  (optional, --json flag)

docs/
  source_access.md           Validated source findings + cURL commands

tests/
  test_normalizers.py
  test_dedupe.py
  test_ranker.py
  test_storage.py
```

---

## How `config/sources.yaml` Works

`config/sources.yaml` is the **canonical registry** for all monitored sources. Every source behavior is driven from this file — the code contains no hardcoded source logic.

Each entry supports:

| Field                  | Description |
|------------------------|-------------|
| `id`                   | Unique machine identifier (used in CLI `--source` flag) |
| `name`                 | Display name in reports |
| `enabled`              | Set to `false` to skip without deleting the entry |
| `trust_weight`         | Float 0.0–1.0, feeds into scoring |
| `source_type`          | `"official"` (lab/company blog) or `"reported"` (journalism) |
| `section_url`          | Monitored section URL |
| `feed_url`             | RSS/Atom URL, or `null` if none |
| `access_method`        | `rss` / `rss_with_ua` / `html_nextjs` |
| `requires_user_agent`  | `true` to use a browser UA header |
| `article_url_patterns` | Expected article URL patterns (for documentation) |
| `listing_strategy`     | `rss` or `html_nextjs` |
| `parser_type`          | `rss_standard` or `anthropic` |
| `notes`                | Caveats, validation findings |
| `curl_examples`        | Shell commands for manual inspection |

### Adding a new source

1. Inspect the source URL manually (see `docs/source_access.md` for the pattern).
2. Add a new entry to `config/sources.yaml`.
3. Add documentation to `docs/source_access.md`.
4. If a custom parser is needed, add it to `news_monitor/parsers.py` and register in `PARSER_MAP`.
5. Test: `python main.py --source new_id --since 7d`

---

## How Source Validation Works

Before implementation, every source was inspected using live web fetches to determine:

- Whether an RSS/Atom feed exists and is valid
- Whether the HTML listing is accessible without a browser UA
- What the article URL patterns look like
- Whether bot protection is active

All findings are recorded in `docs/source_access.md` with cURL commands. The `notes` and `curl_examples` fields in `sources.yaml` carry this information forward for maintainers.

---

## Scoring Model

Each article receives a score 0–100 from five additive components:

| Component             | Max | Notes |
|-----------------------|-----|-------|
| Source trust weight   | 20  | `trust_weight * 20` |
| Official source bonus | 15  | +15 if `source_type == "official"` |
| Topic relevance       | 30  | Weighted keyword hits across matched topics |
| Multi-source coverage | 20  | `(num_sources - 1) * 10`, capped at 20 |
| Novelty               | 15  | +15 if URL not seen before |

**Labels:**

| Label    | Score |
|----------|-------|
| critical | ≥ 80  |
| high     | ≥ 65  |
| medium   | ≥ 50  |
| low      | < 50  |

Default runs include `medium` and above. Use `--include-low` or `--min-score 0` to see everything.

---

## Deduplication Model

Articles are deduplicated in four layers:

1. **Normalized URL fingerprint** — exact URL match (O(1) hash lookup against DB)
2. **Normalized title fingerprint** — exact normalized title match (O(1))
3. **Fuzzy title similarity** — `difflib.SequenceMatcher` ratio ≥ 0.72
4. **Keyword Jaccard similarity** — shared meaningful words / union ≥ 0.30

After dedup, articles covering the same event are merged into `EventCluster` objects. The **canonical** article is chosen by: official source > highest trust weight > most recent date.

---

## State Model

State is persisted in `data/app_state.db` (SQLite). Tables:

| Table           | Purpose |
|-----------------|---------|
| `runs`          | One row per run; tracks status, window, counts |
| `seen_articles` | URL/title fingerprints of all processed articles |
| `event_clusters`| Deduplicated event fingerprints with member URLs |
| `source_health` | Per-source success/failure tracking |

**Atomic run pattern:** A run row is inserted with `status=in_progress` at start. It's only updated to `success` after all state writes complete. If the process crashes, the run stays `in_progress` and is ignored by `get_last_successful_run()`. The next run recalculates the window from the previous successful run.

---

## CLI Usage

```bash
# Install dependencies
pip install -r requirements.txt

# First run (auto-detects first run, uses 14-day lookback)
python main.py

# Later runs (auto-uses last successful run as window start)
python main.py

# Override lookback window
python main.py --since 7d
python main.py --since 48h

# Limit terminal output to top 10
python main.py --top 10

# Include low-priority items
python main.py --include-low

# Filter to a specific category
python main.py --category model_releases
python main.py --category safety_evals

# Run only specific sources
python main.py --source openai_news,anthropic_news,deepmind_blog

# Also write a JSON export
python main.py --json

# Clear state and re-run from scratch (14-day window)
python main.py --force-full-refresh

# Suppress terminal output (only write report files)
python main.py --quiet

# List all configured sources
python main.py --list-sources
```

### Categories (for `--category` filter)

- `model_releases`
- `product_launches`
- `agents_tooling`
- `research`
- `infrastructure`
- `policy_regulation`
- `safety_evals`
- `funding_ma`
- `enterprise_adoption`
- `other`

---

## Running Tests

```bash
pip install pytest
pytest tests/
```

---

## Report Format

Reports are saved to `reports/YYYY-MM-DD-ai-news-summary.md`. Each entry includes:

- Title with link
- Source, date, score, label, category
- Summary paragraph (1-2 sentences, extracted from feed)
- 2-4 bullet points
- Why it matters
- Related coverage (other sources reporting the same event)

---

## Requirements

- Python ≥ 3.10
- Internet access to fetch RSS/HTML sources
- Dependencies: `feedparser`, `requests`, `beautifulsoup4`, `PyYAML`, `python-dateutil`
