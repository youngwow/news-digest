#!/usr/bin/env python3
"""
RSS / Atom news scraper.
Reads sources.json, fetches feeds via httpx + feedparser,
deduplicates by URL, and writes raw_news.json.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

# ── config ──────────────────────────────────────────────────────────
SOURCES_FILE = os.path.join(os.path.dirname(__file__), "sources.json")
OUTPUT_FILE  = os.path.join(os.path.dirname(__file__), "raw_news.json")

REQUEST_TIMEOUT = 20          # seconds per HTTP request
MAX_REDIRECTS   = 5           # follow up to 5 redirects
DATE_WINDOW_HOURS = 24        # keep only articles published within this window
USER_AGENT      = (
    "Mozilla/5.0 (compatible; NewsDigestBot/1.0; +https://github.com/hermes)"
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("scraper")


# ── helpers ─────────────────────────────────────────────────────────

def load_sources(path: str) -> list[dict]:
    """Load source list from JSON file. Returns list of source dicts."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Sources file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("sources", [])


def fetch_feed(client: httpx.Client, url: str) -> str | None:
    """Fetch RSS/Atom feed content. Returns raw XML or None on failure."""
    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException:
        log.warning("Timeout fetching %s", url)
        return None
    except httpx.HTTPStatusError as e:
        log.warning("HTTP %s fetching %s", e.response.status_code, url)
        return None
    except httpx.RequestError as e:
        log.warning("Request error for %s: %s", url, e)
        return None

    content_type = resp.headers.get("content-type", "")
    # Reject non-XML / non-RSS responses (some sites return HTML on error)
    if "xml" not in content_type and "rss" not in content_type and "atom" not in content_type:
        # Many feeds serve as text/xml; also accept missing content-type
        if content_type and "html" in content_type:
            log.warning("Skipping %s — got HTML instead of feed (content-type: %s)", url, content_type)
            return None

    return resp.text


def parse_entry(entry, source_name: str) -> dict:
    """Extract fields from a feedparser entry."""
    # Date: try published, fall back to updated
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        date_str = datetime(*published[:6], tzinfo=timezone.utc).isoformat()
    else:
        date_str = None

    # Description: prefer summary, fall back to subtitle or description
    summary = entry.get("summary") or entry.get("subtitle") or entry.get("description") or ""

    return {
        "title":       entry.get("title", "").strip(),
        "url":         entry.get("link", "").strip(),
        "published":   date_str,
        "summary":     strip_html(summary).strip()[:500],   # first 500 chars
        "source":      source_name,
    }


def strip_html(text: str) -> str:
    """Remove HTML tags (best-effort)."""
    import re
    return re.sub(r"<[^>]*>", "", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def is_within_window(date_str: str | None, window_hours: int = DATE_WINDOW_HOURS) -> bool:
    """Return True if date_str is within the last `window_hours` hours."""
    if date_str is None:
        return False
    try:
        pub_dt = datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    return pub_dt >= cutoff


def is_boilerplate(entry: dict) -> bool:
    """Return True if this entry is boilerplate / filler, not a real article."""
    url = entry.get("url", "")

    # BBC live-feed wrappers (generic summary, no real article content)
    if "/live/" in url:
        return True

    # BBC podcast pages (not news articles)
    if "/podcasts/" in url:
        return True

    # Entries with null date AND generic boilerplate summary
    if entry.get("published") is None:
        summary = entry.get("summary", "")
        generic_patterns = [
            "Последние новости, комментарии и видео",
            "latest news and updates",
        ]
        if any(p.lower() in summary.lower() for p in generic_patterns):
            return True

    return False


def deduplicate(articles: list[dict]) -> list[dict]:
    """Deduplicate articles by URL, keeping the first occurrence."""
    seen: set[str] = set()
    result = []
    for a in articles:
        url = a["url"]
        if url and url not in seen:
            seen.add(url)
            result.append(a)
    return result


# ── main ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Loading sources from %s", SOURCES_FILE)
    sources = load_sources(SOURCES_FILE)
    log.info("Found %d sources", len(sources))

    articles: list[dict] = []
    success_count = 0
    fail_count = 0

    with httpx.Client(
        timeout=REQUEST_TIMEOUT,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for src in sources:
            name = src.get("name", "unknown")
            url  = src.get("rss", "")
            if not url:
                log.warning("Skipping %s — no RSS URL", name)
                continue

            log.info("Fetching %s (%s)", name, url)
            xml = fetch_feed(client, url)
            if xml is None:
                fail_count += 1
                continue

            feed = feedparser.parse(xml)
            if feed.bozo and not feed.entries:
                log.warning("Bozo feed for %s: %s", name, feed.bozo_exception)
                fail_count += 1
                continue
            if feed.bozo:
                log.info("%s: feedparser warning (bozo) but entries exist — using them", name)

            for entry in feed.entries:
                article = parse_entry(entry, name)
                if not article["url"]:   # skip entries with no link
                    continue
                if is_boilerplate(article):
                    log.info("  → boilerplate skipped: %s", article["url"])
                    continue
                articles.append(article)

            success_count += 1
            log.info("  → %s: %d entries", name, len(feed.entries))

    log.info("Fetched %d articles from %d sources (%d failed)",
             len(articles), success_count, fail_count)

    # Date filter — keep only articles published within DATE_WINDOW_HOURS
    before_date_filter = len(articles)
    articles = [a for a in articles if is_within_window(a.get("published"))]
    log.info("After date filter (24h window): %d articles (dropped %d)",
             len(articles), before_date_filter - len(articles))

    # Deduplicate
    articles = deduplicate(articles)
    log.info("After dedup: %d unique articles", len(articles))

    # Build output
    output = {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "total":        len(articles),
        "sources_ok":   success_count,
        "sources_fail": fail_count,
        "articles":     articles,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info("Written %s (%d articles)", OUTPUT_FILE, len(articles))


if __name__ == "__main__":
    main()
