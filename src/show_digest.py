#!/usr/bin/env python3
"""
Pretty-print a current or archived digest in the terminal.

Usage:
    show_digest.py                       # current data/digest.json
    show_digest.py --list                # list snapshots in data/digests/
    show_digest.py 2026-05-17T18-16      # show snapshot by timestamp prefix
    show_digest.py --latest 3            # show last N snapshots (separator between)
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from format_telegram import format_digest
from utils import DATA_DIR, get_logger

DIGEST_JSON = os.path.join(DATA_DIR, "digest.json")
DIGESTS_DIR = os.path.join(DATA_DIR, "digests")
log = get_logger("show_digest")


def _snapshot_paths() -> list[str]:
    """Return snapshot paths sorted oldest → newest (by filename, which is ISO-ish)."""
    if not os.path.isdir(DIGESTS_DIR):
        return []
    files = [f for f in os.listdir(DIGESTS_DIR)
             if f.startswith("digest-") and f.endswith(".json")]
    return [os.path.join(DIGESTS_DIR, f) for f in sorted(files)]


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _print_digest(path: str) -> None:
    data = _load_json(path)
    print(format_digest(data))


def _cmd_list() -> int:
    paths = _snapshot_paths()
    if not paths:
        print(f"(no snapshots in {DIGESTS_DIR})")
        return 0
    for p in paths:
        ts = os.path.basename(p).removeprefix("digest-").removesuffix(".json")
        try:
            data = _load_json(p)
            headline = data.get("digest", {}).get("headline", "?")
        except (json.JSONDecodeError, OSError):
            headline = "(unreadable)"
        print(f"{ts}  {headline[:100]}")
    return 0


def _resolve_snapshot(query: str) -> str | None:
    """Find a snapshot whose timestamp starts with `query`."""
    for p in _snapshot_paths():
        ts = os.path.basename(p).removeprefix("digest-").removesuffix(".json")
        if ts.startswith(query):
            return p
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="View current or archived digests")
    parser.add_argument("snapshot", nargs="?", help="Snapshot timestamp prefix (e.g. 2026-05-17T18-16)")
    parser.add_argument("--list", action="store_true", help="List all snapshots")
    parser.add_argument("--latest", type=int, default=0, help="Show last N snapshots")
    args = parser.parse_args()

    if args.list:
        return _cmd_list()

    if args.latest:
        paths = _snapshot_paths()[-args.latest:]
        if not paths:
            print(f"(no snapshots in {DIGESTS_DIR})")
            return 0
        for i, p in enumerate(paths):
            if i > 0:
                print("\n" + "─" * 60 + "\n")
            _print_digest(p)
        return 0

    if args.snapshot:
        path = _resolve_snapshot(args.snapshot)
        if path is None:
            log.error("no snapshot matches '%s' in %s", args.snapshot, DIGESTS_DIR)
            return 1
        _print_digest(path)
        return 0

    if not os.path.exists(DIGEST_JSON):
        log.error("current digest not found at %s — run ./run.sh first", DIGEST_JSON)
        return 1
    _print_digest(DIGEST_JSON)
    return 0


if __name__ == "__main__":
    sys.exit(main())
