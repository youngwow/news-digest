"""Content-addressed cache of classified chunk outputs, namespaced by config.

The key folds in provider/model/temperature/prompt so switching the active
provider, the model, or editing the prompt invalidates stale entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from ..jsonio import get_logger, load_json, save_json

log = get_logger("chunk_cache")


class ChunkCache:
    def __init__(self, cache_dir: str, namespace: str):
        self.cache_dir = cache_dir
        self.namespace = namespace

    def key(self, articles: list[dict]) -> str:
        blob = json.dumps(articles, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(self.namespace.encode("utf-8") + b"\x00" + blob).hexdigest()

    def _path(self, key: str) -> str:
        return os.path.join(self.cache_dir, f"{key}.json")

    def get(self, articles: list[dict]) -> dict | None:
        path = self._path(self.key(articles))
        if not os.path.exists(path):
            return None
        try:
            return load_json(path)
        except (ValueError, OSError):
            return None

    def put(self, articles: list[dict], output: dict) -> None:
        os.makedirs(self.cache_dir, exist_ok=True)
        try:
            save_json(self._path(self.key(articles)), output)
        except OSError as e:
            log.warning("failed to write cache entry: %s", e)

    def prune(self, retention_days: int) -> None:
        if not os.path.isdir(self.cache_dir):
            return
        cutoff = time.time() - retention_days * 86400
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
            log.info("chunk_cache: pruned %d expired entries", removed)
