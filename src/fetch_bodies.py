#!/usr/bin/env python3
"""
Optional pre-classification article-body enrichment.

scraper.py calls enrich_articles() between dedup and write if
config.scraper.fetch_bodies is true. Each article URL is fetched once,
its <p> text extracted with BeautifulSoup, and the result cached at
data/body_cache/<sha256(url)>.txt for re-use across runs. Bodies are
stored on the article dict in-place as the `body` field.
"""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock

import httpx
from bs4 import BeautifulSoup

from utils import CONFIG, DATA_DIR, get_logger

CACHE_DIR = os.path.join(DATA_DIR, "body_cache")

_scraper = CONFIG["scraper"]
BODY_MAX_CHARS               = _scraper["body_max_chars"]
BODY_TIMEOUT                 = _scraper["body_timeout"]
BODY_CONCURRENCY             = _scraper["body_concurrency"]
BODY_CACHE_RETENTION_DAYS    = _scraper["body_cache_retention_days"]
USER_AGENT                   = _scraper["user_agent"]

log = get_logger("fetch_bodies")

_stats = {"fetched": 0, "cache_hits": 0, "errors": 0}
_stats_lock = Lock()


# ── HTML extraction ────────────────────────────────────────────────

_PREFERRED_CONTAINERS = ("article", "main", "body")
_SKIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form")


def extract_paragraphs(html: str) -> str:
    """Extract <p> text from the most specific container available.

    Strategy: prefer <article>, then <main>, then <body>. Within the chosen
    container, concatenate <p> text. Noisy elements (script/style/nav/etc.)
    are stripped before extraction.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:  # noqa: BLE001
        log.debug("HTML parse error: %s", e)
        return ""

    for tag in soup(_SKIP_TAGS):
        tag.decompose()

    for name in _PREFERRED_CONTAINERS:
        container = soup.find(name)
        if container is None:
            continue
        paragraphs = [p.get_text(" ", strip=True) for p in container.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if paragraphs:
            return "\n\n".join(paragraphs)
    return ""


# ── network + cache ────────────────────────────────────────────────

def _cache_path(url: str) -> str:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{key}.txt")


def _read_cache(url: str) -> str | None:
    path = _cache_path(url)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write_cache(url: str, body: str) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    try:
        with open(_cache_path(url), "w", encoding="utf-8") as f:
            f.write(body)
    except OSError as e:
        log.warning("failed to write body cache for %s: %s", url, e)


def _prune_cache() -> None:
    if not os.path.isdir(CACHE_DIR):
        return
    cutoff = (datetime.now() - timedelta(days=BODY_CACHE_RETENTION_DAYS)).timestamp()
    removed = 0
    for entry in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, entry)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("body_cache: pruned %d expired entries", removed)


def fetch_body(client: httpx.Client, url: str, max_chars: int = BODY_MAX_CHARS) -> str | None:
    """Fetch URL, extract paragraph text, truncate. Returns None on any failure."""
    cached = _read_cache(url)
    if cached is not None:
        with _stats_lock:
            _stats["cache_hits"] += 1
        return cached[:max_chars]

    try:
        resp = client.get(url, follow_redirects=True)
        resp.raise_for_status()
    except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as e:
        log.debug("body fetch failed for %s: %s", url, e)
        with _stats_lock:
            _stats["errors"] += 1
        return None

    content_type = resp.headers.get("content-type", "")
    if "html" not in content_type.lower():
        with _stats_lock:
            _stats["errors"] += 1
        return None

    body = extract_paragraphs(resp.text)
    if not body:
        with _stats_lock:
            _stats["errors"] += 1
        return None

    truncated = body[:max_chars]
    _write_cache(url, truncated)
    with _stats_lock:
        _stats["fetched"] += 1
    return truncated


# ── orchestration ──────────────────────────────────────────────────

def enrich_articles(articles: list[dict], concurrency: int = BODY_CONCURRENCY) -> None:
    """Mutate articles in-place, adding `body` to each with a successful fetch."""
    _prune_cache()
    _stats["fetched"] = 0
    _stats["cache_hits"] = 0
    _stats["errors"] = 0

    if not articles:
        return

    with httpx.Client(
        timeout=BODY_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(fetch_body, client, a["url"]): a for a in articles if a.get("url")}
            for fut in as_completed(futures):
                article = futures[fut]
                try:
                    body = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.debug("unhandled body fetch error for %s: %s", article.get("url"), e)
                    body = None
                article["body"] = body or ""

    log.info("Bodies fetched: %d/%d, cache hits: %d, errors: %d",
             _stats["fetched"] + _stats["cache_hits"], len(articles),
             _stats["cache_hits"], _stats["errors"])
