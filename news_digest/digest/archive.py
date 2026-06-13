"""DigestArchive: timestamped digest snapshots — write, prune, browse, collect.

Consolidates snapshot I/O that was scattered across the old _archive_digest,
show_digest, and weekly_rollup.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from ..jsonio import get_logger, load_json, save_json
from ..models import DigestDocument, Story

log = get_logger("archive")


class DigestArchive:
    def __init__(self, digests_dir: str, retention_days: int = 30):
        self.dir = digests_dir
        self.retention_days = retention_days

    @staticmethod
    def timestamp_of(filename: str) -> datetime | None:
        """Parse 'digest-2026-05-17T18-16.json' → aware UTC datetime, or None."""
        stem = filename.removeprefix("digest-").removesuffix(".json")
        try:
            return datetime.strptime(stem, "%Y-%m-%dT%H-%M").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def write(self, doc: DigestDocument | dict) -> str:
        os.makedirs(self.dir, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M")
        path = os.path.join(self.dir, f"digest-{ts}.json")
        save_json(path, doc.to_dict() if isinstance(doc, DigestDocument) else doc)
        log.info("Archived digest snapshot → %s", path)
        self.prune()
        return path

    def prune(self) -> int:
        if not os.path.isdir(self.dir):
            return 0
        cutoff = time.time() - self.retention_days * 86400
        removed = 0
        for entry in os.listdir(self.dir):
            path = os.path.join(self.dir, entry)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
        if removed:
            log.info("digests: pruned %d snapshots older than %d days",
                     removed, self.retention_days)
        return removed

    def snapshot_paths(self) -> list[str]:
        """Snapshot paths sorted oldest → newest (filenames are ISO-ish)."""
        if not os.path.isdir(self.dir):
            return []
        files = [f for f in os.listdir(self.dir)
                 if f.startswith("digest-") and f.endswith(".json")]
        return [os.path.join(self.dir, f) for f in sorted(files)]

    def resolve(self, prefix: str) -> str | None:
        """Find a snapshot whose timestamp starts with `prefix`."""
        for p in self.snapshot_paths():
            ts = os.path.basename(p).removeprefix("digest-").removesuffix(".json")
            if ts.startswith(prefix):
                return p
        return None

    def load(self, path: str) -> DigestDocument:
        return DigestDocument.from_dict(load_json(path))

    def recent_stories(self, lookback_days: int, now: datetime | None = None) -> list[Story]:
        """All stories from snapshots dated within the last `lookback_days` days."""
        if not os.path.isdir(self.dir):
            return []
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=lookback_days)
        stories: list[Story] = []
        for name in sorted(os.listdir(self.dir)):
            if not (name.startswith("digest-") and name.endswith(".json")):
                continue
            ts = self.timestamp_of(name)
            if ts is None or ts < cutoff:
                continue
            try:
                data = load_json(os.path.join(self.dir, name))
            except (ValueError, OSError) as e:
                log.warning("skipping unreadable snapshot %s: %s", name, e)
                continue
            stories.extend(Story.from_dict(s) for s in data.get("all_stories", []))
        return stories
