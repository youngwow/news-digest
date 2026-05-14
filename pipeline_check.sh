#!/usr/bin/env bash
# Pipeline watchdog — independent health monitoring for the news digest.
# Designed to run as a separate cron job (e.g., every 30 min).
# Alerts when pipeline outputs are stale, missing, or broken.
#
# Exit codes:
#   0 — healthy (no output emitted → silent, cron delivers nothing)
#   1 — warnings (output emitted as alert)

set -euo pipefail

HEALTH_CHECK="/root/hermes-work-dir/projects/news-digest/health_check.py"

# Run the health check, capture JSON output
REPORT=$("$HEALTH_CHECK" --json 2>/dev/null) || true

if [ -z "$REPORT" ]; then
    # Health check itself failed — that's a meta-problem
    echo "⚠️ Мониторинг дайджеста — сбой"
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

# Something is wrong — emit alert
echo "⚠️ Дайджест новостей — проблемы"
echo ""

# Parse and format issues
python3 << 'PYEOF'
import json, sys

report = json.loads(sys.argv[1])

for name, check in report["checks"].items():
    desc = {
        "raw_news": "Скрапинг RSS",
        "analyzed_news": "Дедупликация и анализ",
        "digest_json": "Digest JSON",
        "digest_md": "Digest Markdown",
        "pipeline_log": "Лог пайплайна"
    }.get(name, name)

    status = check["status"]
    if status == "healthy":
        continue

    icon = "❌" if status == "critical" else "⚠️"
    print(f"{icon} {desc}: {status}")

    for severity, msg in check.get("issues", []):
        tag = "КРИТ" if severity == "critical" else "ПРЕД"
        print(f"  [{tag}] {msg}")
    print("")
PYEOF "$REPORT"

echo "—"
echo "Детали: /root/hermes-work-dir/projects/news-digest/health.json"
exit 1
