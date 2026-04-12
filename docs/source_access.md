# Source Access Documentation

> Validated 2026-04-12.
> This document records how each source was inspected, what access method was
> determined, and the exact cURL commands needed to reproduce manual fetches.
> Update this file whenever a source's behavior changes.

---

## Table of Contents

1. [OpenAI News](#1-openai-news)
2. [OpenAI Research](#2-openai-research)
3. [Anthropic News](#3-anthropic-news)
4. [Anthropic Research](#4-anthropic-research)
5. [Google DeepMind Blog](#5-google-deepmind-blog)
6. [Hugging Face Blog](#6-hugging-face-blog)
7. [Ars Technica AI](#7-ars-technica-ai)
8. [TechCrunch AI](#8-techcrunch-ai)
9. [Semafor](#9-semafor)
10. [WIRED Artificial Intelligence](#10-wired-artificial-intelligence)
11. [The Information (Disabled)](#11-the-information-disabled)

---

## 1. OpenAI News

| Field           | Value |
|-----------------|-------|
| Source ID       | `openai_news` |
| Section URL     | https://openai.com/news/ |
| Feed URL        | https://openai.com/news/rss.xml |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- **HTML blocked:** `GET https://openai.com/news/` returns HTTP 403. No HTML scraping possible.
- **RSS confirmed:** `GET https://openai.com/news/rss.xml` returns a valid RSS 2.0 feed. Validated live feed entries include April 2026 items.
- **Feed mixes content types:** The feed includes news posts, research announcements, and "academy" (tutorial) posts. Topic classifier handles relevance filtering.
- **Article URL patterns:** `/index/[slug]`, `/news/[slug]`, `/research/[slug]`, `/academy/[slug]`

### cURL commands

```bash
# Fetch the RSS feed (recommended)
curl -L "https://openai.com/news/rss.xml"

# Attempt HTML listing (currently 403)
curl -L "https://openai.com/news/"

# Sample article
curl -L "https://openai.com/index/axios-developer-tool-compromise"
```

---

## 2. OpenAI Research

| Field           | Value |
|-----------------|-------|
| Source ID       | `openai_research` |
| Section URL     | https://openai.com/research/ |
| Feed URL        | https://openai.com/news/rss.xml (shared) |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- Same HTML 403 restriction as openai_news.
- No separate research RSS feed exists. Research posts appear in the main `/news/rss.xml` feed.
- The pipeline uses the same feed for both `openai_news` and `openai_research`. Deduplication ensures no duplicate articles appear in reports.

### cURL commands

```bash
# Shared with openai_news
curl -L "https://openai.com/news/rss.xml"
```

---

## 3. Anthropic News

| Field           | Value |
|-----------------|-------|
| Source ID       | `anthropic_news` |
| Section URL     | https://www.anthropic.com/news |
| Feed URL        | None (no official RSS) |
| Access method   | `html_nextjs` |
| Requires UA     | No |
| Parser          | `anthropic` |

### Validation findings

- **No RSS feed:** Anthropic does not publish an official RSS or Atom feed as of 2026-04-12. Community workarounds exist but are unreliable.
- **HTML accessible:** `GET https://www.anthropic.com/news` returns full HTML without blocking. No anti-bot measures detected.
- **Next.js SSR:** The page is built with Next.js. Article metadata is embedded in a `<script id="__NEXT_DATA__">` tag as inline JSON, including titles, dates, and URL slugs.
- **Article URL patterns:**
  - Primary: `https://www.anthropic.com/news/[slug]`
  - Some top-level: `https://www.anthropic.com/[slug]` (e.g., `/glasswing`)
- **Live items validated:** claude-sonnet-4-6, claude-opus-4-6, 81k-interviews, glasswing

### cURL commands

```bash
# Fetch the news listing page (HTML)
curl -L "https://www.anthropic.com/news"

# With browser User-Agent (use if plain fetch fails in future)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://www.anthropic.com/news"

# Sample article page
curl -L "https://www.anthropic.com/news/claude-sonnet-4-6"
```

### Parser notes

The `anthropic` parser (`parsers.py: AnthropicParser`) uses two strategies:
1. **Primary:** Extract `__NEXT_DATA__` JSON, recursively search for dicts with title + url fields
2. **Fallback:** Scan `<a href>` links matching `/news/*` or `/research/*` patterns

If Anthropic redesigns their site, update the `_find_article_dicts` search keys in `parsers.py`.

---

## 4. Anthropic Research

| Field           | Value |
|-----------------|-------|
| Source ID       | `anthropic_research` |
| Section URL     | https://www.anthropic.com/research |
| Feed URL        | None |
| Access method   | `html_nextjs` |
| Requires UA     | No |
| Parser          | `anthropic` |

### Validation findings

- Same Next.js structure as Anthropic News.
- Page lists 60+ publications with dates, category tags (Policy, Alignment, Economic Research, Science), and summaries.
- Article URL pattern: `https://www.anthropic.com/research/[slug]`

### cURL commands

```bash
curl -L "https://www.anthropic.com/research"

# Sample research paper
curl -L "https://www.anthropic.com/research/emotion-concepts-function"
```

---

## 5. Google DeepMind Blog

| Field           | Value |
|-----------------|-------|
| Source ID       | `deepmind_blog` |
| Section URL     | https://deepmind.google/discover/blog/ |
| Feed URL        | https://deepmind.google/blog/rss.xml |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- **RSS confirmed:** `GET https://deepmind.google/blog/rss.xml` returns a valid RSS 2.0 feed.
- **Validated live items:** Gemma 4 (2026-04-02), Gemini 3.1 Flash Live, AGI progress framework
- **Article URL patterns:** `https://deepmind.google/blog/[slug]/` and some cross-posts to `https://blog.google/innovation-and-ai/[slug]`
- No anti-bot blocking detected on the RSS endpoint.

### cURL commands

```bash
# Fetch the RSS feed
curl -L "https://deepmind.google/blog/rss.xml"

# Landing page (JavaScript-rendered, use RSS instead)
curl -L "https://deepmind.google/discover/blog/"

# Sample article
curl -L "https://deepmind.google/blog/gemma-4-byte-for-byte-the-most-capable-open-models/"
```

---

## 6. Hugging Face Blog

| Field           | Value |
|-----------------|-------|
| Source ID       | `huggingface_blog` |
| Section URL     | https://huggingface.co/blog |
| Feed URL        | https://huggingface.co/blog/feed.xml |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- **RSS confirmed:** `GET https://huggingface.co/blog/feed.xml` returns valid RSS 2.0 with Atom namespace.
- **Validated live items:** Waypoint-1.5, Multimodal Embedding with Sentence Transformers, Safetensors joins PyTorch Foundation, Gemma 4 welcome post
- **High volume feed:** HF blog includes community posts from external authors (`/blog/{author-username}/{slug}`). Not all are AI frontier news. Ranker/classifier handles filtering.
- **HTML is JS-rendered:** Only the RSS feed is reliable for automated access.

### cURL commands

```bash
# Fetch the RSS feed
curl -L "https://huggingface.co/blog/feed.xml"

# HTML listing (JS-rendered; use RSS instead)
curl -L "https://huggingface.co/blog"

# Sample article
curl -L "https://huggingface.co/blog/gemma4"
```

---

## 7. Ars Technica AI

| Field           | Value |
|-----------------|-------|
| Source ID       | `arstechnica_ai` |
| Section URL     | https://arstechnica.com/ai/ |
| Feed URL        | https://arstechnica.com/ai/feed/ |
| Access method   | `rss_with_ua` |
| Requires UA     | Yes |
| Parser          | `rss_standard` |

### Validation findings

- **HTML and feed blocked without UA:** Direct `GET` to `arstechnica.com` returned an error (likely 403 or bot redirect).
- **RSS works with browser UA:** The feed at `/ai/feed/` is well-documented in the RSS community and has been validated by multiple third-party readers. The `feedparser` library with a browser User-Agent header successfully fetches it.
- **Alternative full-site feed:** `https://feeds.arstechnica.com/arstechnica/index`
- **Article URL pattern:** `https://arstechnica.com/ai/YYYY/MM/[slug]/`

### cURL commands

```bash
# Fetch AI section RSS (with browser UA — required)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://arstechnica.com/ai/feed/"

# Full site feed (alternative)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://feeds.arstechnica.com/arstechnica/index"

# AI section HTML (with UA)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://arstechnica.com/ai/"
```

### Failure mode

If Ars Technica blocks the UA, the source will log a failure and be marked in source health. It degrades gracefully — the run continues with other sources.

---

## 8. TechCrunch AI

| Field           | Value |
|-----------------|-------|
| Source ID       | `techcrunch_ai` |
| Section URL     | https://techcrunch.com/category/artificial-intelligence/ |
| Feed URL        | https://techcrunch.com/category/artificial-intelligence/feed/ |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- **RSS confirmed:** `GET https://techcrunch.com/category/artificial-intelligence/feed/` returns a valid RSS 2.0 feed. Validated April 2026 items.
- **HTML has Turnstile bot protection:** The HTML listing uses Cloudflare Turnstile for bot detection. Only the RSS feed is used for automated access.
- **Article URL pattern:** `https://techcrunch.com/YYYY/MM/DD/[slug]/`
- **High volume:** Roughly 10-20 articles/day in this category. Topic classifier filters for relevance.

### cURL commands

```bash
# Fetch AI category RSS feed
curl -L "https://techcrunch.com/category/artificial-intelligence/feed/"

# HTML listing (protected by Turnstile — for manual inspection only)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://techcrunch.com/category/artificial-intelligence/"
```

---

## 9. Semafor

| Field           | Value |
|-----------------|-------|
| Source ID       | `semafor` |
| Section URL     | https://www.semafor.com/vertical/tech |
| Feed URL        | https://www.semafor.com/rss.xml |
| Access method   | `rss` |
| Requires UA     | No |
| Parser          | `rss_standard` |

### Validation findings

- **RSS confirmed:** `GET https://www.semafor.com/rss.xml` returns a valid RSS 2.0 feed. Validated April 2026 items including AI-relevant stories (radiologists + AI, Amazon/Nvidia chips, Anthropic revenue).
- **General feed (all topics):** The `/rss.xml` feed covers all Semafor topics (finance, politics, tech, etc.). The classifier applies `general_feed_min_matches: 2` to filter for AI-relevant articles only.
- **Tech section RSS:** A section-specific RSS at `/vertical/tech/rss.xml` may exist but was not confirmed. The general feed is reliable and sufficient.
- **Article URL pattern:** `https://www.semafor.com/article/MM/DD/YYYY/[slug]`

### cURL commands

```bash
# General RSS feed
curl -L "https://www.semafor.com/rss.xml"

# Tech vertical landing page
curl -L "https://www.semafor.com/vertical/tech"

# Sample AI article
curl -L "https://www.semafor.com/article/04/10/2026/anthropic-is-gaining-on-openais-revenue-but-hasnt-yet-eclipsed-it"
```

---

## 10. WIRED Artificial Intelligence

| Field           | Value |
|-----------------|-------|
| Source ID       | `wired_ai` |
| Section URL     | https://www.wired.com/tag/artificial-intelligence/ |
| Feed URL        | https://www.wired.com/feed/tag/ai/latest/rss |
| Access method   | `rss_with_ua` |
| Requires UA     | Yes |
| Parser          | `rss_standard` |

### Validation findings

- **Direct fetch blocked:** `GET https://www.wired.com/*` was blocked for headless access during validation. WIRED uses aggressive bot detection.
- **RSS with UA:** The feed `https://www.wired.com/feed/tag/ai/latest/rss` is documented in multiple community RSS readers (Feeder, Readless, etc.) as active. A browser User-Agent is required.
- **Alternative feed:** `https://www.wired.com/feed/category/artificial-intelligence/rss`
- **Article URL pattern:** `https://www.wired.com/story/[slug]/`

### cURL commands

```bash
# Primary feed (with browser UA — required)
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://www.wired.com/feed/tag/ai/latest/rss"

# Alternative feed
curl -L -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
  "https://www.wired.com/feed/category/artificial-intelligence/rss"
```

### Failure mode

WIRED may return HTTP 403 even with a UA. The source health tracker records this. After 3 consecutive failures, the source is flagged in the report's Source Health section. The run continues with other sources.

---

## 11. The Information (Disabled)

| Field           | Value |
|-----------------|-------|
| Source ID       | `the_information` |
| Section URL     | https://www.theinformation.com/ |
| Feed URL        | https://www.theinformation.com/feed |
| Access method   | `html` |
| Requires UA     | Yes |
| Enabled         | **No** |

### Validation findings

- **Hard paywall:** Site requires login + active subscription. The homepage shows `"isLoggedIn":false` and content is gated.
- **Bot detection:** Cloudflare and reCAPTCHA are active. Automated access is impractical and likely violates ToS.
- **RSS exists:** A feed at `/feed` exists but article bodies are paywalled; only titles and teasers are available.
- **Recommendation:** Do not enable without a plan to handle authenticated sessions. If you have a subscription, a custom parser using saved session cookies could work, but this is out of scope for the default setup.

### cURL commands

```bash
# Inspect homepage (will show paywall state)
curl -L "https://www.theinformation.com/"

# RSS feed (titles/teasers only; no article body)
curl -L "https://www.theinformation.com/feed"

# Note: full content requires authenticated session cookie
```

---

## Adding a New Source

1. Validate the source manually using the cURL patterns above.
2. Add a new entry to `config/sources.yaml` following the schema.
3. Add a new section to this document with validated findings.
4. If the source needs a custom parser, implement it in `news_monitor/parsers.py` and register it in `PARSER_MAP`.
5. Test by running `python main.py --source new_source_id --since 7d`.
