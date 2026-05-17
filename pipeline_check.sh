#!/usr/bin/env bash
# Pipeline watchdog — independent health monitoring for the news digest.
# Designed to run as a separate cron job (e.g., every 30 min).
# Alerts when pipeline outputs are stale, missing, or broken.
#
# Exit codes:
#   0 — healthy (no output emitted → silent, cron delivers nothing)
#   1 — warnings (output emitted as alert)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HEALTH_CHECK="$SCRIPT_DIR/src/health_check.py"
HEALTH_JSON="$SCRIPT_DIR/data/health.json"

# Run the health check, capture JSON output
REPORT=$(python3 "$HEALTH_CHECK" --json 2>/dev/null) || true

if [ -z "$REPORT" ]; then
    # Health check itself failed — that's a meta-problem
    echo "ALERT: ⚠️ Мониторинг дайджеста — сбой"
    echo ""
    echo "health_check.py не смог выполниться. Возможные причины:"
    echo "— Python или зависимости не установлены"
    echo "— Файлы проекта повреждены"
    echo "— Диск переполнен"
    exit 1
fi

# If healthy, stay silent (exit 0 with no stdout → cron delivers nothing)
if echo "$REPORT" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d['overall']=='healthy' else 1)" 2>/dev/null; then
    exit 0
fi

# Something is wrong — emit alert.
# Every line is prefixed with "ALERT:" so it survives cron MAILTO filtering and
# `grep ALERT data/alerts.log` queries.
echo "ALERT: ⚠️ Дайджест новостей — проблемы"
echo ""

python3 - "$REPORT" << 'PYEOF'
import json, sys

report = json.loads(sys.argv[1])

for name, check in report["checks"].items():
    desc = {
        "raw_news": "Скрапинг RSS",
        "classified": "Классификация LLM",
        "digest_json": "Digest JSON",
        "digest_md": "Digest Markdown",
        "pipeline_log": "Лог пайплайна",
        "pipeline_status": "Шаги пайплайна",
        "source_metrics": "Здоровье источников",
    }.get(name, name)

    status = check["status"]
    if status == "healthy":
        continue

    icon = "❌" if status == "critical" else "⚠️"
    print(f"ALERT: {icon} {desc}: {status}")

    for severity, msg in check.get("issues", []):
        tag = "КРИТ" if severity == "critical" else "ПРЕД"
        print(f"ALERT:   [{tag}] {msg}")
    print("")
PYEOF

echo "—"
echo "Детали: ${HEALTH_JSON}"
exit 1
