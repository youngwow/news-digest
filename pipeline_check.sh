#!/usr/bin/env bash
# Pipeline watchdog — thin wrapper over the news_digest package.
# Designed to run as a separate cron job (e.g., every 30 min): runs the health
# check, prints an ALERT block when unhealthy (and pushes it to Telegram if
# telegram.alerts is enabled), stays silent when healthy.
#
# Exit codes:
#   0 — healthy (no output → cron delivers nothing)
#   1 — problems (ALERT block emitted)

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec python3 -m news_digest watchdog
