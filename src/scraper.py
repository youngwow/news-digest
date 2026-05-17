#!/usr/bin/env python3
"""
RSS / Atom news scraper.
Reads sources.json, fetches feeds via httpx + feedparser,
deduplicates by URL, and writes raw_news.json.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from utils import CONFIG, DATA_DIR, get_logger, load_seen_urls, save_json

# ── config ──────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_FILE = os.path.join(_ROOT, "sources.json")
OUTPUT_FILE  = os.path.join(DATA_DIR, "raw_news.json")
METRICS_FILE = os.path.join(DATA_DIR, "source_metrics.json")

_scraper          = CONFIG["scraper"]
REQUEST_TIMEOUT   = _scraper["request_timeout"]
MAX_REDIRECTS     = _scraper["max_redirects"]
DATE_WINDOW_HOURS = _scraper["date_window_hours"]
USER_AGENT        = _scraper["user_agent"]
FETCH_BODIES      = _scraper["fetch_bodies"]
BODY_CONCURRENCY  = _scraper["body_concurrency"]
CROSS_RUN_ENABLED = CONFIG["dedup"]["cross_run_enabled"]

EMA_ALPHA = 0.3   # how much weight new observation gets in rolling averages

log = get_logger("scraper")


# ── helpers ─────────────────────────────────────────────────────────

def load_sources(path: str) -> list[dict]:
    """Load source list from JSON file. Returns list of source dicts."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Sources file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("sources", [])


def fetch_feed(client: httpx.Client, name: str, url: str) -> str | None:
    """Fetch RSS/Atom feed content. Returns raw XML or None on failure."""
    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException:
        log.warning("Timeout fetching %s (%s)", name, url)
        return None
    except httpx.HTTPStatusError as e:
        log.warning("HTTP %s fetching %s (%s)", e.response.status_code, name, url)
        return None
    except httpx.RequestError as e:
        log.warning("Request error for %s (%s): %s", name, url, e)
        return None

    content_type = resp.headers.get("content-type", "")
    if "xml" not in content_type and "rss" not in content_type and "atom" not in content_type:
        if content_type and "html" in content_type:
            log.warning("Skipping %s — got HTML instead of feed (content-type: %s)", name, content_type)
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
        "summary":     clean_summary(strip_html(summary).strip())[:500],   # first 500 chars
        "source":      source_name,
    }


def strip_html(text: str) -> str:
    """Remove HTML tags (best-effort)."""
    return re.sub(r"<[^>]*>", "", text).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


# Patterns that indicate WordPress/RSS boilerplate to strip from summaries
_BOILERPLATE_PATTERNS = [
    # English WordPress footers
    re.compile(r"\s*The\s+post\s+.+?\s+first\s+appeared\s+on\s+.+?\.?\s*$", re.IGNORECASE),
    # Russian WordPress footers
    re.compile(r"\s*Сообщение\s+.+?\s+появились\s+сначала\s+на\s+.+?\.?\s*$", re.IGNORECASE),
    # "Читать далее" / "Read more" + duplicated text
    re.compile(r"\s*Читать\s+(далее|полностью)[….]*\s*$", re.IGNORECASE),
    re.compile(r"\s*Read\s+more[….]*\s*$", re.IGNORECASE),
    # "Source:" / "Источник:" footers
    re.compile(r"\s*(Source|Источник)\s*:.*$", re.IGNORECASE),
    # Trailing "→"/"»" that's just a link marker
    re.compile(r"\s*[→»]\s*$"),
]


def clean_summary(text: str) -> str:
    """Strip WordPress/RSS boilerplate footers from summary text."""
    for pat in _BOILERPLATE_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


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


def filter_cross_run(articles: list[dict]) -> list[dict]:
    """Drop articles whose URLs are present in seen_urls.json (delivered in a recent digest)."""
    if not CROSS_RUN_ENABLED:
        return articles
    seen = load_seen_urls()
    if not seen:
        return articles
    kept = [a for a in articles if a.get("url") not in seen]
    dropped = len(articles) - len(kept)
    if dropped:
        log.info("Cross-run dedup: dropped %d URLs already delivered in a recent digest", dropped)
    return kept


# ── per-source metrics ──────────────────────────────────────────────

def _ema(prev: float | None, current: float, alpha: float = EMA_ALPHA) -> float:
    """Single-step exponential moving average; seeds from `current` on first sample."""
    if prev is None:
        return float(current)
    return alpha * float(current) + (1 - alpha) * float(prev)


def _load_source_metrics() -> dict[str, dict]:
    if not os.path.exists(METRICS_FILE):
        return {}
    try:
        with open(METRICS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def update_source_metrics(samples: list[dict]) -> None:
    """Merge per-source observations from this run into data/source_metrics.json.

    Each sample: {name, success: bool, latency_ms: float, article_count: int}.
    """
    metrics = _load_source_metrics()
    now = datetime.now(timezone.utc).isoformat()

    for s in samples:
        name = s["name"]
        entry = metrics.get(name, {})
        entry["fetches_total"]      = entry.get("fetches_total", 0) + 1
        entry["successes_total"]    = entry.get("successes_total", 0) + (1 if s["success"] else 0)
        entry["success_rate_ema"]   = _ema(entry.get("success_rate_ema"),
                                            1.0 if s["success"] else 0.0)
        entry["avg_latency_ms_ema"] = _ema(entry.get("avg_latency_ms_ema"), s["latency_ms"])
        if s["success"]:
            entry["last_success"]       = now
            entry["avg_articles_ema"]   = _ema(entry.get("avg_articles_ema"), s["article_count"])
        else:
            entry["last_failure"] = now
        metrics[name] = entry

    save_json(METRICS_FILE, metrics)


# ── main ────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Loading sources from %s", SOURCES_FILE)
    sources = load_sources(SOURCES_FILE)
    log.info("Found %d sources", len(sources))

    articles: list[dict] = []
    success_count = 0
    fail_count = 0
    samples: list[dict] = []

    valid_sources = [s for s in sources if s.get("rss")]
    for s in sources:
        if not s.get("rss"):
            log.warning("Skipping %s — no RSS URL", s.get("name", "unknown"))

    def _timed_fetch(client_, name_, url_):
        t0 = time.monotonic()
        xml = fetch_feed(client_, name_, url_)
        return xml, (time.monotonic() - t0) * 1000.0

    with httpx.Client(
        timeout=REQUEST_TIMEOUT,
        max_redirects=MAX_REDIRECTS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        with ThreadPoolExecutor(max_workers=min(10, len(valid_sources) or 1)) as pool:
            futures = {
                pool.submit(_timed_fetch, client, s["name"], s["rss"]): s
                for s in valid_sources
            }
            for fut in as_completed(futures):
                src = futures[fut]
                name = src.get("name", "unknown")
                xml, latency_ms = fut.result()
                if xml is None:
                    fail_count += 1
                    samples.append({"name": name, "success": False,
                                    "latency_ms": latency_ms, "article_count": 0})
                    continue

                feed = feedparser.parse(xml)
                if feed.bozo and not feed.entries:
                    log.warning("Bozo feed for %s: %s", name, feed.bozo_exception)
                    fail_count += 1
                    samples.append({"name": name, "success": False,
                                    "latency_ms": latency_ms, "article_count": 0})
                    continue
                if feed.bozo:
                    log.info("%s: feedparser warning (bozo) but entries exist — using them", name)

                kept = 0
                for entry in feed.entries:
                    article = parse_entry(entry, name)
                    if not article["url"]:
                        continue
                    if is_boilerplate(article):
                        log.info("  → boilerplate skipped: %s", article["url"])
                        continue
                    articles.append(article)
                    kept += 1

                success_count += 1
                samples.append({"name": name, "success": True,
                                "latency_ms": latency_ms, "article_count": kept})
                log.info("  → %s: %d entries (%d kept, %.0f ms)",
                         name, len(feed.entries), kept, latency_ms)

    update_source_metrics(samples)
    log.info("Fetched %d articles from %d sources (%d failed)",
             len(articles), success_count, fail_count)

    # Date filter — keep only articles published within DATE_WINDOW_HOURS
    no_date = sum(1 for a in articles if a.get("published") is None)
    before_date_filter = len(articles)
    articles = [a for a in articles if is_within_window(a.get("published"))]
    outside_window = before_date_filter - len(articles) - no_date
    log.info(
        f"After date filter ({DATE_WINDOW_HOURS}h window): %d articles (dropped %d: %d had no date, %d outside window)",
        len(articles), before_date_filter - len(articles), no_date, outside_window,
    )

    # Deduplicate (within-run)
    articles = deduplicate(articles)
    log.info("After dedup: %d unique articles", len(articles))

    # Cross-run dedup — skip URLs delivered in a recent digest
    articles = filter_cross_run(articles)

    # Optional body enrichment (off by default; flip config.scraper.fetch_bodies)
    if FETCH_BODIES and articles:
        from fetch_bodies import enrich_articles
        enrich_articles(articles, concurrency=BODY_CONCURRENCY)

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
