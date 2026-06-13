#!/usr/bin/env bash
# News digest pipeline runner — thin wrapper over the news_digest package.
# The orchestration (locking, step gating, status/history) lives in
# news_digest.pipeline.Pipeline; see `python3 -m news_digest run --help`.
#
# Usage:  ./run.sh [--resume]
# Output: compact Telegram-friendly digest on stdout; status on stderr.
# Exit:   0 if every step passed (or a lock conflict no-op), 1 otherwise.

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 -m news_digest run "$@"
