# CLAUDE.md — Developer Notes for AI Assistants

This file documents architecture decisions, non-obvious design choices, and
guidance for future AI-assisted development on this project.

---

## What this project does

`JPsAINewsReview` is a local Python CLI tool that:
1. Fetches news from 10+ AI-focused sources (RSS + HTML scraping)
2. Filters, deduplicates, and clusters articles by event
3. Scores each event 0-100 for noteworthiness
4. Writes a Markdown report to `reports/`

---

## Key architectural rules

### 1. `config/sources.yaml` is the source of truth

All source behavior is driven from this YAML file. Do not hardcode source
URLs, parser types, or access methods in Python files. If you need to change
how a source is fetched, update `sources.yaml` and, if needed, add a new
parser to `parsers.py`.

### 2. Every source must be validated before implementation

Before adding a new source or changing fetch logic, use live web inspection
to verify:
- Whether an RSS feed exists
- Whether the HTML is accessible
- What the article URL patterns look like
- Whether a browser UA is needed

Document findings in `docs/source_access.md` and `sources.yaml`.

### 3. Graceful degradation on source failures

Source fetch errors are caught in `source_registry.py: _fetch_one()` and
stored in `SourceHealth`. They never crash the run. Check the Source Health
section of any report for failure details.

### 4. Atomic state writes

The run lifecycle in `storage.py`:
1. `begin_run()` — writes `status=in_progress`
2. Pipeline runs
3. `finish_run(status="success")` — only called on clean exit

If the process crashes, `get_last_successful_run()` returns the previous
successful run, so the window is recalculated correctly next time.

### 5. No LLM calls in the pipeline

The summarizer, classifier, and ranker are all keyword/rule-based. There are
no API calls to Claude or any other LLM during a run. This keeps the tool
fast, free to run, and offline-friendly.

---

## File responsibilities

| File                          | Responsibility |
|-------------------------------|---------------|
| `main.py`                     | CLI arg parsing, pipeline orchestration |
| `news_monitor/config.py`      | Load and validate YAML configs |
| `news_monitor/source_registry.py` | Fetch dispatch per source |
| `news_monitor/fetchers.py`    | HTTP I/O only (RSS + HTML) |
| `news_monitor/parsers.py`     | Convert raw bytes to RawArticle objects |
| `news_monitor/normalizers.py` | Pure transformations, no I/O |
| `news_monitor/dedupe.py`      | Fingerprinting, fuzzy match, clustering |
| `news_monitor/ranker.py`      | Score calculation (0-100) |
| `news_monitor/classifier.py`  | Category + topic keyword matching |
| `news_monitor/summarizer.py`  | Extractive summary, bullets, why |
| `news_monitor/storage.py`     | SQLite read/write only |
| `news_monitor/reporter.py`    | Format and write output files |
| `news_monitor/models.py`      | Dataclasses only, minimal logic |
| `news_monitor/utils.py`       | Shared pure functions |

---

## Adding a new source parser

1. Validate the source (curl + live inspection)
2. Add to `config/sources.yaml` with `parser_type: my_parser`
3. Implement class `MyParser` in `parsers.py` with a `parse()` method
4. Register in `PARSER_MAP = {..., "my_parser": MyParser()}`
5. Add dispatch logic in `source_registry.py: _dispatch()` if needed
6. Document in `docs/source_access.md`

---

## Scoring model

See `news_monitor/ranker.py` for the exact formula. Summary:
- Trust weight × 20 (max 20)
- Official source bonus: +15
- Topic relevance: up to +30
- Multi-source coverage: up to +20
- Novelty: +15

Labels: critical ≥ 80, high ≥ 65, medium ≥ 50, low < 50

---

## Common maintenance tasks

### A source's RSS feed URL changed

1. Update `feed_url` in `config/sources.yaml`
2. Update `docs/source_access.md`
3. Log in `WORKLOG.md`

### A source starts blocking requests

1. Set `requires_user_agent: true` in `sources.yaml`
2. Change `access_method` from `rss` to `rss_with_ua`
3. Log in `WORKLOG.md`

### Anthropic redesigns their site

The `AnthropicParser` in `parsers.py` uses `_find_article_dicts()` to
recursively search `__NEXT_DATA__` JSON for article-shaped dicts. If the
structure changes:
1. Inspect the new `__NEXT_DATA__` JSON structure manually
2. Update the field names searched in `_extract_url()`, `_extract_title()`,
   `_extract_date()`
3. Test: `python main.py --source anthropic_news --since 7d`

---

## Testing

```bash
pytest tests/
```

Tests cover: URL normalization, title normalization, dedup layers,
clustering, scoring, and SQLite state persistence.

Tests do not make live network calls. They use only local fixtures.

---

## Dependencies (minimal by design)

- `feedparser` — RSS/Atom parsing (lenient on malformed XML)
- `requests` — HTTP with retry logic
- `beautifulsoup4` — HTML parsing (Anthropic parser)
- `PyYAML` — config loading
- `python-dateutil` — date parsing helper

No vector databases, no embeddings, no LLM API calls.
