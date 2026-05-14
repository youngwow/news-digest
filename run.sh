#!/usr/bin/env bash
# News digest pipeline runner
# Full pipeline: scrape → dedup → analyze → format → output to stdout
# Usage: ./run.sh
# Output: compact Telegram-friendly digest on stdout

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') MSK — starting pipeline ===" >&2

# Step 1: Scrape — fetch RSS/Atom feeds, write raw_news.json
echo "[1/4] Scraping RSS feeds..." >&2
python3 "$SCRIPT_DIR/scraper.py" >&2

# Step 2: Dedup — group similar articles, write analyzed_news.json
echo "[2/4] Deduplicating articles..." >&2
python3 "$SCRIPT_DIR/analyzer.py" --phase dedup >&2

# Step 3: Analyze — classify, score, generate digest.json + digest.md
echo "[3/4] Analyzing (classification + importance + entity extraction)..." >&2
python3 "$SCRIPT_DIR/analyze_full.py" >&2

# Step 4: Format — Telegram-friendly markdown to stdout
echo "[4/4] Formatting for Telegram..." >&2
python3 "$SCRIPT_DIR/format_telegram.py"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') MSK — pipeline complete ===" >&2
