"""
AI News Monitor - Local Web Server

Serves a browser UI at http://localhost:5000

  python web_server.py          # start server (opens browser automatically)
  python web_server.py --port 8080  # custom port
  python web_server.py --no-browser # don't auto-open browser

API routes:
  GET    /                        Serve the UI
  GET    /api/reports             List all reports (metadata)
  GET    /api/reports/<filename>  Get rendered HTML of a specific report
  DELETE /api/reports/<filename>  Delete a report and its run history
  POST   /api/history/clear       Wipe all seen-article history (keeps report files)
  POST   /api/run                 Start a new pipeline run
  GET    /api/run/status          Poll current run state
  GET    /api/sources             List all configured sources with health status
  POST   /api/sources             Add a new source to sources.yaml
  PATCH  /api/sources/<id>        Toggle a source enabled/disabled
  DELETE /api/sources/<id>        Remove a source from sources.yaml
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_from_directory
from news_monitor.storage import delete_run, clear_seen_state, init_db, get_all_source_health

# ─── Paths ────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
REPORTS_DIR = PROJECT_ROOT / "reports"
TEMPLATES_DIR = PROJECT_ROOT / "templates"
SOURCES_FILE = PROJECT_ROOT / "config" / "sources.yaml"
LAST_RUN_LOG = PROJECT_ROOT / "data" / "last_run.log"

# ─── App ──────────────────────────────────────────────────────────────────────

app = Flask(__name__, static_folder=None)

# ─── Run state (thread-safe via lock) ─────────────────────────────────────────

_run_lock = threading.Lock()
_run_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "output": [],
}


# ─── Routes: UI ───────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return send_from_directory(str(TEMPLATES_DIR), "index.html")


# ─── Routes: Reports ──────────────────────────────────────────────────────────


@app.route("/api/reports")
def list_reports():
    """Return a list of all report files with metadata."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob("*.md"), reverse=True)

    reports = []
    for f in files:
        reports.append(_report_meta(f))

    return jsonify({"reports": reports})


@app.route("/api/reports/<path:filename>")
def get_report(filename: str):
    """Return a report rendered as HTML."""
    # Security: only allow .md files from REPORTS_DIR
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        return jsonify({"error": "invalid file"}), 400

    path = REPORTS_DIR / safe_name
    if not path.exists():
        return jsonify({"error": "not found"}), 404

    raw_md = path.read_text(encoding="utf-8")
    html = _render_markdown(raw_md)

    return jsonify({
        "filename": safe_name,
        "html": html,
        **_report_meta(path),
    })


@app.route("/api/reports/<path:filename>", methods=["DELETE"])
def delete_report(filename: str):
    """
    Delete a report file and remove its run record + seen articles from the DB.
    This allows the same articles to surface again on the next run.
    """
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        return jsonify({"error": "invalid file"}), 400

    path = REPORTS_DIR / safe_name
    if not path.exists():
        return jsonify({"error": "not found"}), 404

    # Remove run record and seen_articles from DB
    result = delete_run(str(path))

    # Delete the report file itself
    try:
        path.unlink()
    except Exception as exc:
        return jsonify({"error": f"could not delete file: {exc}"}), 500

    return jsonify({
        "deleted": safe_name,
        "run_id": result.get("run_id"),
        "articles_removed": result.get("articles_removed", 0),
        "run_found": result.get("found", False),
    })


# ─── Routes: Sources ─────────────────────────────────────────────────────────


def _load_sources_yaml() -> dict:
    """Load sources.yaml and return the raw dict."""
    with open(SOURCES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_sources_yaml(data: dict) -> None:
    """Write sources.yaml back to disk."""
    with open(SOURCES_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _slugify(name: str) -> str:
    """Turn a display name into a safe source ID."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "_", slug)
    return slug[:40]


@app.route("/api/sources")
def list_sources():
    """Return all configured sources merged with their latest health data."""
    try:
        raw = _load_sources_yaml()
        health_map = {h.source_id: h for h in get_all_source_health()}

        sources = []
        for s in raw.get("sources", []):
            sid = s.get("id", "")
            h = health_map.get(sid)
            sources.append({
                "id":               sid,
                "name":             s.get("name", sid),
                "enabled":          s.get("enabled", True),
                "source_type":      s.get("source_type", "reported"),
                "trust_weight":     s.get("trust_weight", 0.5),
                "feed_url":         s.get("feed_url"),
                "homepage_url":     s.get("homepage_url", ""),
                "access_method":    s.get("access_method", "rss"),
                "requires_user_agent": s.get("requires_user_agent", False),
                "parser_type":      s.get("parser_type", "rss_standard"),
                "notes":            s.get("notes", ""),
                # health
                "last_checked":     h.last_checked.isoformat() if h and h.last_checked else None,
                "last_success":     h.last_success.isoformat() if h and h.last_success else None,
                "last_error":       h.last_error if h else None,
                "consecutive_failures": h.consecutive_failures if h else 0,
                "total_articles_fetched": h.total_articles_fetched if h else 0,
            })
        return jsonify({"sources": sources})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sources", methods=["POST"])
def add_source():
    """Add a new source to sources.yaml."""
    body = request.get_json(silent=True) or {}

    name = (body.get("name") or "").strip()
    feed_url = (body.get("feed_url") or "").strip() or None
    homepage_url = (body.get("homepage_url") or "").strip()
    source_type = body.get("source_type", "reported")
    trust_weight = float(body.get("trust_weight", 0.7))
    requires_ua = bool(body.get("requires_user_agent", False))
    custom_id = (body.get("id") or "").strip()

    if not name:
        return jsonify({"error": "name is required"}), 400
    if not feed_url:
        return jsonify({"error": "feed_url is required"}), 400

    try:
        raw = _load_sources_yaml()
        existing_ids = {s.get("id") for s in raw.get("sources", [])}

        source_id = custom_id if custom_id else _slugify(name)
        # Ensure uniqueness
        base_id, n = source_id, 2
        while source_id in existing_ids:
            source_id = f"{base_id}_{n}"
            n += 1

        access_method = "rss_with_ua" if requires_ua else "rss"
        new_source = {
            "id":                 source_id,
            "name":               name,
            "enabled":            True,
            "trust_weight":       round(trust_weight, 2),
            "source_type":        source_type,
            "homepage_url":       homepage_url or feed_url,
            "section_url":        homepage_url or feed_url,
            "feed_url":           feed_url,
            "access_method":      access_method,
            "requires_user_agent": requires_ua,
            "article_url_patterns": [],
            "listing_strategy":   "rss",
            "parser_type":        "rss_standard",
            "notes":              "Added via web UI.",
            "curl_examples":      [f'curl -L "{feed_url}"'],
        }

        raw.setdefault("sources", []).append(new_source)
        _save_sources_yaml(raw)
        return jsonify({"added": source_id, "source": new_source})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sources/<source_id>", methods=["PATCH"])
def toggle_source(source_id: str):
    """Toggle a source enabled/disabled."""
    body = request.get_json(silent=True) or {}
    try:
        raw = _load_sources_yaml()
        for s in raw.get("sources", []):
            if s.get("id") == source_id:
                if "enabled" in body:
                    s["enabled"] = bool(body["enabled"])
                else:
                    s["enabled"] = not s.get("enabled", True)
                _save_sources_yaml(raw)
                return jsonify({"id": source_id, "enabled": s["enabled"]})
        return jsonify({"error": "source not found"}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/sources/<source_id>", methods=["DELETE"])
def delete_source(source_id: str):
    """Remove a source from sources.yaml entirely."""
    try:
        raw = _load_sources_yaml()
        sources = raw.get("sources", [])
        original_count = len(sources)
        raw["sources"] = [s for s in sources if s.get("id") != source_id]
        if len(raw["sources"]) == original_count:
            return jsonify({"error": "source not found"}), 404
        _save_sources_yaml(raw)
        return jsonify({"deleted": source_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Routes: History ─────────────────────────────────────────────────────────


@app.route("/api/history/clear", methods=["POST"])
def clear_history():
    """
    Wipe all seen-article history and run records so the next run
    treats everything as new (14-day first-run lookback).
    Report files on disk are NOT deleted.
    """
    try:
        init_db()
        # Clear seen articles and clusters
        clear_seen_state()
        # Also wipe the runs table so last-successful-run returns None
        from news_monitor.storage import _db
        with _db() as conn:
            conn.execute("DELETE FROM runs")
        return jsonify({"cleared": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Routes: Run ──────────────────────────────────────────────────────────────


@app.route("/api/run", methods=["POST"])
def start_run():
    """Start a new pipeline run in a background thread."""
    with _run_lock:
        if _run_state["running"]:
            return jsonify({"error": "A run is already in progress"}), 409

        _run_state["running"] = True
        _run_state["started_at"] = _iso_now()
        _run_state["finished_at"] = None
        _run_state["exit_code"] = None
        _run_state["output"] = []

    # Parse optional args from request body
    body = request.get_json(silent=True) or {}
    extra_args = []
    if body.get("force_refresh"):
        extra_args.append("--force-full-refresh")
    if body.get("since"):
        extra_args += ["--since", body["since"]]

    thread = threading.Thread(
        target=_run_pipeline,
        args=(extra_args,),
        daemon=True,
    )
    thread.start()

    return jsonify({"started": True, "started_at": _run_state["started_at"]})


@app.route("/api/run/status")
def run_status():
    """Return the current run state (for polling)."""
    with _run_lock:
        state = dict(_run_state)
    return jsonify(state)


# ─── Background pipeline runner ───────────────────────────────────────────────


def _run_pipeline(extra_args: list[str]) -> None:
    """Run main.py in a subprocess. Updates _run_state on completion."""
    cmd = [sys.executable, str(PROJECT_ROOT / "main.py")] + extra_args
    output_lines: list[str] = []

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        for line in proc.stdout:
            clean = line.rstrip()
            output_lines.append(clean)
        proc.wait()
        exit_code = proc.returncode
    except Exception as exc:
        output_lines.append(f"[ERROR] Failed to start pipeline: {exc}")
        exit_code = -1

    with _run_lock:
        _run_state["running"] = False
        _run_state["finished_at"] = _iso_now()
        _run_state["exit_code"] = exit_code
        _run_state["output"] = output_lines


@app.route("/api/run/log")
def run_log():
    """Return the full last_run.log file contents."""
    try:
        if LAST_RUN_LOG.exists():
            lines = LAST_RUN_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
            return jsonify({"lines": lines})
        return jsonify({"lines": []})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ─── Markdown renderer ────────────────────────────────────────────────────────


def _render_markdown(md_text: str) -> str:
    """
    Convert Markdown to HTML.
    Uses the markdown library if available; falls back to a minimal converter.
    Post-processes links to open in new tab.
    """
    try:
        import markdown
        html = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        )
    except ImportError:
        html = _minimal_md_to_html(md_text)

    # Make all links open in new tab
    html = re.sub(
        r'<a href="([^"]+)"',
        r'<a href="\1" target="_blank" rel="noopener noreferrer"',
        html,
    )

    return html


def _minimal_md_to_html(md: str) -> str:
    """Very basic Markdown → HTML fallback (no external deps)."""
    lines = md.split("\n")
    out = []
    in_ul = False

    for line in lines:
        # Headings
        if line.startswith("### "):
            if in_ul: out.append("</ul>"); in_ul = False
            content = _inline_md(line[4:])
            out.append(f"<h3>{content}</h3>")
        elif line.startswith("## "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<h1>{_inline_md(line[2:])}</h1>")
        # Horizontal rule
        elif line.strip() == "---":
            if in_ul: out.append("</ul>"); in_ul = False
            out.append("<hr>")
        # Bullet list
        elif line.startswith("- "):
            if not in_ul: out.append("<ul>"); in_ul = True
            out.append(f"<li>{_inline_md(line[2:])}</li>")
        # Empty line
        elif line.strip() == "":
            if in_ul: out.append("</ul>"); in_ul = False
            out.append("")
        else:
            if in_ul: out.append("</ul>"); in_ul = False
            out.append(f"<p>{_inline_md(line)}</p>")

    if in_ul:
        out.append("</ul>")

    return "\n".join(out)


def _inline_md(text: str) -> str:
    """Process inline Markdown: bold, code, links."""
    # Links: [text](url)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        text,
    )
    # Bold: **text**
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    # Inline code: `text`
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


# ─── Report metadata helper ───────────────────────────────────────────────────


def _report_meta(path: Path) -> dict:
    """Extract display metadata from a report file."""
    name = path.name

    # Parse date from filename: YYYY-MM-DD-ai-news-summary.md
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})", name)
    date_str = date_match.group(1) if date_match else "Unknown date"

    # Parse a human-friendly date
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        display_date = dt.strftime("%b %d, %Y")
    except ValueError:
        display_date = date_str

    # Quick scan: count h3 headings (= article entries) and check for metadata
    item_count = 0
    sources_checked = None
    try:
        text = path.read_text(encoding="utf-8")
        item_count = text.count("\n### ")
        # Try to extract "Sources checked: N" from metadata
        m = re.search(r"\*\*Sources checked:\*\* (\d+)", text)
        if m:
            sources_checked = int(m.group(1))
    except Exception:
        pass

    return {
        "filename": name,
        "date": date_str,
        "display_date": display_date,
        "item_count": item_count,
        "sources_checked": sources_checked,
        "size_bytes": path.stat().st_size,
        "modified_at": datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ─── Entry point ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AI News Monitor Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    url = f"http://{args.host}:{args.port}"
    print(f"\n  AI News Monitor running at {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
