#!/usr/bin/env bash
# Сторож для cron (например, каждые 30 минут): молчит, пока всё хорошо; иначе печатает
# блок ALERT и, при telegram.alerts, шлёт его в чат (одинаковые подряд подавляются).
# Exit: 0 — здорово, 1 — есть проблемы.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec uv run python -m src watchdog "$@"
