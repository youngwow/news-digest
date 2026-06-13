#!/usr/bin/env python3
"""
Aggregate archived digest snapshots into a "week in review" digest.

Reads data/digests/digest-YYYY-MM-DDTHH-MM.json snapshots from the last N days,
merges their stories (cross-snapshot dedup via merge_chunks.dedup_stories),
rebuilds a digest with analyze_full.build_digest, and prints it via
format_telegram.format_digest. Read-only: no archiving, no seen-URL marking.

    python3 src/weekly_rollup.py              # last 7 days
    python3 src/weekly_rollup.py --days 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from analyze_full import build_digest
from format_telegram import format_digest
from merge_chunks import dedup_stories
from utils import DATA_DIR, get_logger

DIGESTS_DIR = os.path.join(DATA_DIR, "digests")
log = get_logger("weekly_rollup")


def snapshot_timestamp(filename: str) -> datetime | None:
    """Parse 'digest-2026-05-17T18-16.json' → aware UTC datetime, or None."""
    stem = filename.removeprefix("digest-").removesuffix(".json")
    try:
        return datetime.strptime(stem, "%Y-%m-%dT%H-%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def collect_stories(digests_dir: str = DIGESTS_DIR, days: int = 7,
                    now: datetime | None = None) -> list[dict]:
    """Return all stories from snapshots dated within the last `days` days."""
    if not os.path.isdir(digests_dir):
        return []
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    stories: list[dict] = []
    for name in sorted(os.listdir(digests_dir)):
        if not (name.startswith("digest-") and name.endswith(".json")):
            continue
        ts = snapshot_timestamp(name)
        if ts is None or ts < cutoff:
            continue
        path = os.path.join(digests_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("skipping unreadable snapshot %s: %s", name, e)
            continue
        snap_stories = data.get("all_stories", [])
        stories.extend(snap_stories)
        log.info("%s: %d stories", name, len(snap_stories))
    return stories


def build_rollup(digests_dir: str = DIGESTS_DIR, days: int = 7,
                 now: datetime | None = None) -> dict | None:
    """Merge snapshots into a digest-shaped dict, or None when no stories found."""
    stories = collect_stories(digests_dir, days, now)
    if not stories:
        return None
    deduped = dedup_stories(stories)
    log.info("Rollup: %d stories → %d after dedup (last %d days)",
             len(stories), len(deduped), days)
    digest = build_digest(deduped, datetime.now(timezone.utc).isoformat())
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rollup_days": days,
        "total_raw": len(stories),
        "total_unique": len(deduped),
        "digest": digest,
        "all_stories": deduped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate archived digest snapshots into a rollup digest")
    parser.add_argument("--days", type=int, default=7, help="Window in days (default 7)")
    args = parser.parse_args()

    full = build_rollup(days=args.days)
    if full is None:
        print(f"(no digest snapshots within the last {args.days} days in {DIGESTS_DIR})")
        return 1
    print(format_digest(full))
    return 0


if __name__ == "__main__":
    sys.exit(main())
