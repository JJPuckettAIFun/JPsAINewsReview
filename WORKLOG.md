# WORKLOG.md — Development Log

This log records source validation decisions, architecture choices, and
maintenance history. Newest entries at the top.

---

## 2026-04-12 — Initial build

**Author:** Claude (claude-sonnet-4-6) on behalf of Jeremiah Puckett

### Source validation performed

All 11 sources were inspected via live web fetch before implementation.
Findings below:

| Source | Method | Finding |
|--------|--------|---------|
| OpenAI News | RSS | HTML 403. RSS at `/news/rss.xml` confirmed working. Mixed content types. |
| OpenAI Research | RSS | No separate RSS. Shares `/news/rss.xml` with openai_news. |
| Anthropic News | HTML (Next.js) | No RSS feed. Next.js SSR; `__NEXT_DATA__` JSON contains article metadata. |
| Anthropic Research | HTML (Next.js) | Same as Anthropic News. 60+ publications visible. |
| Google DeepMind | RSS | RSS at `deepmind.google/blog/rss.xml` confirmed working. April 2026 items validated. |
| Hugging Face Blog | RSS | RSS at `/blog/feed.xml` confirmed working. High-volume mixed content. |
| Ars Technica AI | RSS with UA | HTML and feed blocked without UA. RSS at `/ai/feed/` works with browser UA. |
| TechCrunch AI | RSS | RSS at `/category/artificial-intelligence/feed/` confirmed. HTML has Turnstile bot protection. |
| Semafor | RSS | General RSS at `/rss.xml` confirmed. April 2026 AI stories visible. General feed needs topic filtering. |
| WIRED AI | RSS with UA | Both HTML and RSS blocked without UA. Community-confirmed RSS at `/feed/tag/ai/latest/rss`. |
| The Information | Disabled | Hard paywall + Cloudflare bot detection. Not practical for automated access. |

### Architecture decisions

- **SQLite over JSON for state** — SQLite provides atomic writes, better query support, and reliable concurrent access. JSON state files can corrupt on crash.
- **feedparser over raw XML parsing** — feedparser is battle-tested, lenient on malformed feeds, and handles RSS 2.0, Atom, and RSS 1.0 transparently.
- **No LLM calls** — Summarization and classification are extractive/keyword-based. This keeps runs fast (~30-60s), free, and offline-friendly. Future enhancement: optional LLM summarization flag.
- **Recursive `__NEXT_DATA__` search for Anthropic** — Rather than hardcoding the exact JSON path (which changes with Next.js deployments), the parser recursively searches for article-shaped dicts. More robust to site updates.
- **config/sources.yaml as source of truth** — All source behavior is driven from YAML. The Python code has no hardcoded source URLs or logic. Adding a source means updating YAML + optionally adding a parser.
- **Union-Find clustering** — Efficient O(n²) worst-case clustering using union-find. Practical for the expected volume (~50-200 articles/run).
- **Four-layer dedup** — URL → title → fuzzy title → keyword Jaccard. Each layer catches a different class of duplicate (same article, same story different headline, same event different angle).

### Initial sources configured

10 sources total; 9 enabled, 1 disabled (The Information).

### Files created

```
main.py
news_monitor/__init__.py
news_monitor/config.py
news_monitor/source_registry.py
news_monitor/fetchers.py
news_monitor/parsers.py
news_monitor/normalizers.py
news_monitor/dedupe.py
news_monitor/ranker.py
news_monitor/classifier.py
news_monitor/summarizer.py
news_monitor/storage.py
news_monitor/reporter.py
news_monitor/models.py
news_monitor/utils.py
config/sources.yaml
config/topics.yaml
docs/source_access.md
tests/__init__.py
tests/test_normalizers.py
tests/test_dedupe.py
tests/test_ranker.py
tests/test_storage.py
requirements.txt
README.md
CLAUDE.md
WORKLOG.md (this file)
```

---

## Future maintenance log

Add new entries here when:
- A source feed URL changes
- A source starts or stops blocking automated access
- A new source is added
- The scoring model is tuned
- Dependencies are upgraded
- Bug fixes or significant changes are made

Format: `YYYY-MM-DD — [description]`
