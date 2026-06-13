"""Cross-run URL dedup state: URLs delivered in recent digests."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Iterable

from ..jsonio import load_json, save_json


class SeenUrlStore:
    """Persists {url: ISO-timestamp} for URLs already delivered, pruning by age.

    The scraper drops articles whose URLs are here; the assemble step records the
    URLs it delivered. Window length is `retention_days`.
    """

    def __init__(self, path: str, retention_days: int):
        self.path = path
        self.retention_days = retention_days

    def load(self) -> dict[str, str]:
        if not os.path.exists(self.path):
            return {}
        try:
            data = load_json(self.path)
        except (ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _prune(self, urls: dict[str, str]) -> dict[str, str]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        kept: dict[str, str] = {}
        for url, ts in urls.items():
            try:
                seen_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError, AttributeError):
                continue
            if seen_at >= cutoff:
                kept[url] = ts
        return kept

    def mark(self, new_urls: Iterable[str]) -> None:
        """Record `new_urls` as delivered now; prune entries older than retention."""
        existing = self.load()
        now = datetime.now(timezone.utc).isoformat()
        for url in new_urls:
            if url and url not in existing:
                existing[url] = now
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        save_json(self.path, self._prune(existing))
