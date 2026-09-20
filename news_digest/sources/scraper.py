"""RSS/Atom scraper: fetch feeds, parse entries, dedup, write raw_news.json."""

from __future__ import annotations

import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from ..config import Config
from ..jsonio import get_logger, load_json, save_json
from ..models import Article
from ..paths import ProjectPaths
from .bodies import BodyEnricher
from .metrics import SourceMetrics
from .seen_urls import SeenUrlStore

log = get_logger("scraper")

# Tracking query params stripped from article URLs — improves URL-based dedup
# and keeps digest links tidy.
_TRACKING_PARAM_PREFIXES = ("utm_", "at_")
_TRACKING_PARAM_NAMES = {"maca", "fbclid", "gclid", "yclid", "ref"}

_BOILERPLATE_PATTERNS = [
    re.compile(r"\s*The\s+post\s+.+?\s+first\s+appeared\s+on\s+.+?\.?\s*$", re.IGNORECASE),
    re.compile(r"\s*Сообщение\s+.+?\s+появились\s+сначала\s+на\s+.+?\.?\s*$", re.IGNORECASE),
    re.compile(r"\s*Читать\s+(далее|полностью)[….]*\s*$", re.IGNORECASE),
    re.compile(r"\s*Read\s+more[….]*\s*$", re.IGNORECASE),
    re.compile(r"\s*(Source|Источник)\s*:.*$", re.IGNORECASE),
    re.compile(r"\s*[→»]\s*$"),
]
_GENERIC_SUMMARIES = ("последние новости, комментарии и видео", "latest news and updates")


def clean_url(url: str) -> str:
    """Strip known tracking query params; leave everything else untouched."""
    if "?" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k not in _TRACKING_PARAM_NAMES
            and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)]
    return urlunsplit(parts._replace(query=urlencode(kept)))


def strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities (best-effort)."""
    return html.unescape(re.sub(r"<[^>]*>", "", text))


def clean_summary(text: str) -> str:
    """Strip WordPress/RSS boilerplate footers from summary text."""
    for pat in _BOILERPLATE_PATTERNS:
        text = pat.sub("", text)
    return text.strip()


def parse_entry(entry, source_name: str) -> Article:
    """Build an Article from a feedparser entry."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    date_str = datetime(*published[:6], tzinfo=timezone.utc).isoformat() if published else None
    summary = entry.get("summary") or entry.get("subtitle") or entry.get("description") or ""
    return Article(
        title=entry.get("title", "").strip(),
        url=clean_url(entry.get("link", "").strip()),
        published=date_str,
        summary=clean_summary(strip_html(summary).strip())[:500],
        source=source_name,
    )


def is_boilerplate(article: Article) -> bool:
    """True if this looks like a live-feed/podcast wrapper or generic filler."""
    if "/live/" in article.url or "/podcasts/" in article.url:
        return True
    if article.published is None:
        s = article.summary.lower()
        return any(p in s for p in _GENERIC_SUMMARIES)
    return False


class FeedParser:
    """Turns raw feed XML into real-article Articles (boilerplate filtered)."""

    def parse(self, xml: str, source_name: str) -> tuple[list[Article], int]:
        """Return (kept articles, total entries seen). Empty list signals a bozo feed."""
        feed = feedparser.parse(xml)
        if feed.bozo and not feed.entries:
            log.warning("Bozo feed for %s: %s", source_name, feed.bozo_exception)
            return [], 0
        if feed.bozo:
            log.info("%s: feedparser warning (bozo) but entries exist — using them", source_name)
        kept: list[Article] = []
        for entry in feed.entries:
            article = parse_entry(entry, source_name)
            if not article.url:
                continue
            if is_boilerplate(article):
                log.info("  → boilerplate skipped: %s", article.url)
                continue
            kept.append(article)
        return kept, len(feed.entries)


class Scraper:
    """Fetches all configured feeds and writes raw_news.json."""

    def __init__(self, config: Config, paths: ProjectPaths):
        self.config = config
        self.paths = paths
        self.parser = FeedParser()
        self.metrics = SourceMetrics(paths.data("source_metrics.json"))
        self.seen = SeenUrlStore(paths.data("seen_urls.json"),
                                 config.dedup.cross_run_retention_days)

    def load_sources(self) -> list[dict]:
        import os
        if not os.path.exists(self.paths.sources_path):
            raise FileNotFoundError(f"Sources file not found: {self.paths.sources_path}")
        return load_json(self.paths.sources_path).get("sources", [])

    @staticmethod
    def feed_url(source: dict) -> str | None:
        """The RSS/Atom URL this pipeline should poll for `source`, or None to skip it.

        sources.json is shared with the platform (`src/`): an entry is polled when
        it is `kind: rss` (its `fetch_url`) and not `enabled: false`. The original
        `rss` key of the first-version schema is still honoured.
        """
        if source.get("enabled") is False:
            return None
        if source.get("rss"):
            return source["rss"]
        if source.get("kind") == "rss" and source.get("fetch_url"):
            return source["fetch_url"]
        return None

    def is_within_window(self, published: str | None) -> bool:
        if published is None:
            return False
        try:
            pub_dt = datetime.fromisoformat(published)
        except (ValueError, TypeError):
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.config.scraper.date_window_hours)
        return pub_dt >= cutoff

    @staticmethod
    def deduplicate(articles: list[Article]) -> list[Article]:
        seen: set[str] = set()
        out = []
        for a in articles:
            if a.url and a.url not in seen:
                seen.add(a.url)
                out.append(a)
        return out

    def filter_cross_run(self, articles: list[Article]) -> list[Article]:
        if not self.config.dedup.cross_run_enabled:
            return articles
        seen = self.seen.load()
        if not seen:
            return articles
        kept = [a for a in articles if a.url not in seen]
        dropped = len(articles) - len(kept)
        if dropped:
            log.info("Cross-run dedup: dropped %d URLs already delivered recently", dropped)
        return kept

    def _fetch_feed(self, client: httpx.Client, name: str, url: str) -> str | None:
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
        ctype = resp.headers.get("content-type", "")
        if "xml" not in ctype and "rss" not in ctype and "atom" not in ctype and "html" in ctype:
            log.warning("Skipping %s — got HTML instead of feed (content-type: %s)", name, ctype)
            return None
        return resp.text

    def run(self) -> dict:
        cfg = self.config.scraper
        log.info("Loading sources from %s", self.paths.sources_path)
        sources = self.load_sources()
        log.info("Found %d sources", len(sources))
        valid = [s for s in sources if self.feed_url(s)]
        for s in sources:
            if s.get("enabled") is False:
                log.info("Skipping %s — disabled in sources.json", s.get("name", "unknown"))
            elif not self.feed_url(s):
                log.info("Skipping %s — not an RSS source (kind=%s)",
                         s.get("name", "unknown"), s.get("kind", "?"))

        articles: list[Article] = []
        samples: list[dict] = []
        success_count = fail_count = 0

        def timed_fetch(client, name, url):
            t0 = time.monotonic()
            xml = self._fetch_feed(client, name, url)
            return xml, (time.monotonic() - t0) * 1000.0

        with httpx.Client(timeout=cfg.request_timeout, max_redirects=cfg.max_redirects,
                          headers={"User-Agent": cfg.user_agent}) as client:
            with ThreadPoolExecutor(max_workers=min(10, len(valid) or 1)) as pool:
                futures = {pool.submit(timed_fetch, client, s["name"], self.feed_url(s)): s
                           for s in valid}
                for fut in as_completed(futures):
                    name = futures[fut].get("name", "unknown")
                    xml, latency_ms = fut.result()
                    if xml is None:
                        fail_count += 1
                        samples.append({"name": name, "success": False,
                                        "latency_ms": latency_ms, "article_count": 0})
                        continue
                    kept, total_entries = self.parser.parse(xml, name)
                    if not kept and total_entries == 0:
                        fail_count += 1
                        samples.append({"name": name, "success": False,
                                        "latency_ms": latency_ms, "article_count": 0})
                        continue
                    articles.extend(kept)
                    success_count += 1
                    samples.append({"name": name, "success": True,
                                    "latency_ms": latency_ms, "article_count": len(kept)})
                    log.info("  → %s: %d entries (%d kept, %.0f ms)",
                             name, total_entries, len(kept), latency_ms)

        self.metrics.update(samples)
        log.info("Fetched %d articles from %d sources (%d failed)",
                 len(articles), success_count, fail_count)

        no_date = sum(1 for a in articles if a.published is None)
        before = len(articles)
        articles = [a for a in articles if self.is_within_window(a.published)]
        log.info("After date filter (%sh window): %d articles (dropped %d: %d no date, %d outside)",
                 cfg.date_window_hours, len(articles), before - len(articles),
                 no_date, before - len(articles) - no_date)

        articles = self.deduplicate(articles)
        log.info("After dedup: %d unique articles", len(articles))
        articles = self.filter_cross_run(articles)

        if cfg.fetch_bodies and articles:
            BodyEnricher(cfg, self.paths.data("body_cache")).enrich(articles)

        output = {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "total": len(articles),
            "sources_ok": success_count,
            "sources_fail": fail_count,
            "articles": [a.to_dict() for a in articles],
        }
        import os
        os.makedirs(self.paths.data_dir, exist_ok=True)
        save_json(self.paths.data("raw_news.json"), output)
        log.info("Written %s (%d articles)", self.paths.data("raw_news.json"), len(articles))
        return output
