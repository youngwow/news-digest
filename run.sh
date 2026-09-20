#!/usr/bin/env bash
# Один запуск конвейера для cron: сбор → обработка → дайджест (в чат при telegram.deliver,
# иначе на stdout). Оркестрация, lock и гейтинг шагов — в src/services/pipeline_run.py.
#
# Usage:  ./run.sh [--limit N] [--no-collect] [--no-process] [--no-digest] [--no-deliver]
# Exit:   0 — чисто (или lock: другой запуск идёт), 2 — деградация, 1 — шаг упал.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
exec uv run python -m src run "$@"
