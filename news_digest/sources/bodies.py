"""Optional pre-classification article-body enrichment (off by default).

When config.scraper.fetch_bodies is true, the Scraper runs BodyEnricher after
cross-run dedup: each URL is fetched once, its <p> text extracted, cached
untruncated, and truncated on read so raising body_max_chars later takes effect.
"""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import httpx
from bs4 import BeautifulSoup

from ..config import ScraperConfig
from ..jsonio import get_logger
from ..models import Article

log = get_logger("body_enricher")

_PREFERRED_CONTAINERS = ("article", "main", "body")
_SKIP_TAGS = ("script", "style", "nav", "header", "footer", "aside", "form")


def extract_paragraphs(html: str) -> str:
    """Extract <p> text from the most specific container (article→main→body)."""
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


class BodyEnricher:
    def __init__(self, config: ScraperConfig, cache_dir: str):
        self.config = config
        self.cache_dir = cache_dir
        self._stats = {"fetched": 0, "cache_hits": 0, "errors": 0}
        self._lock = Lock()

    def _cache_path(self, url: str) -> str:
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir, f"{key}.txt")

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _write_cache(self, url: str, body: str) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        try:
            with open(self._cache_path(url), "w", encoding="utf-8") as f:
                f.write(body)
        except OSError as e:
            log.warning("failed to write body cache for %s: %s", url, e)

    def _prune_cache(self) -> None:
        if not os.path.isdir(self.cache_dir):
            return
        cutoff = time.time() - self.config.body_cache_retention_days * 86400
        removed = 0
        for entry in os.listdir(self.cache_dir):
            path = os.path.join(self.cache_dir, entry)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        if removed:
            log.info("body_cache: pruned %d expired entries", removed)

    def fetch_body(self, client: httpx.Client, url: str) -> str | None:
        """Fetch URL, extract paragraph text, truncate on read. None on failure."""
        max_chars = self.config.body_max_chars
        cached = self._read_cache(url)
        if cached is not None:
            with self._lock:
                self._stats["cache_hits"] += 1
            return cached[:max_chars]
        try:
            resp = client.get(url, follow_redirects=True)
            resp.raise_for_status()
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.RequestError) as e:
            log.debug("body fetch failed for %s: %s", url, e)
            with self._lock:
                self._stats["errors"] += 1
            return None
        if "html" not in resp.headers.get("content-type", "").lower():
            with self._lock:
                self._stats["errors"] += 1
            return None
        body = extract_paragraphs(resp.text)
        if not body:
            with self._lock:
                self._stats["errors"] += 1
            return None
        self._write_cache(url, body)   # cache untruncated
        with self._lock:
            self._stats["fetched"] += 1
        return body[:max_chars]

    def enrich(self, articles: list[Article]) -> None:
        """Set `body` on each article with a successful fetch (in place)."""
        self._prune_cache()
        self._stats = {"fetched": 0, "cache_hits": 0, "errors": 0}
        if not articles:
            return
        with httpx.Client(timeout=self.config.body_timeout,
                          headers={"User-Agent": self.config.user_agent}) as client:
            with ThreadPoolExecutor(max_workers=self.config.body_concurrency) as pool:
                futures = {pool.submit(self.fetch_body, client, a.url): a
                           for a in articles if a.url}
                for fut in as_completed(futures):
                    article = futures[fut]
                    try:
                        body = fut.result()
                    except Exception as e:  # noqa: BLE001
                        log.debug("unhandled body fetch error for %s: %s", article.url, e)
                        body = None
                    article.body = body or ""
        log.info("Bodies fetched: %d/%d, cache hits: %d, errors: %d",
                 self._stats["fetched"] + self._stats["cache_hits"], len(articles),
                 self._stats["cache_hits"], self._stats["errors"])
