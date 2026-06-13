#!/usr/bin/env bash
# News digest pipeline runner.
# Flow: scrape → extract → split → classify → merge → assemble → format → cleanup
#
# Usage:  ./run.sh
# Output: compact Telegram-friendly digest on stdout; status messages on stderr.
# Exit:   0 if every step passed, 1 otherwise.
# Side effect: writes data/pipeline_status.json for monitoring.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Atomic lock: prevent overlapping runs (e.g. cron firing while a manual run is in flight).
# `mkdir` is atomic on all POSIX systems; exit 0 on a lock conflict so cron doesn't treat
# the no-op as a failure. Stale lock can be cleared with `make unlock`.
mkdir -p "$SCRIPT_DIR/data"
LOCK_DIR="$SCRIPT_DIR/data/.run.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Another pipeline run holds the lock at $LOCK_DIR — exiting (no-op)" >&2
    exit 0
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

PIPELINE_START=$(date '+%Y-%m-%d %H:%M:%S MSK')
STATUS_FILE="$SCRIPT_DIR/data/pipeline_status.json"
ALERTS_LOG="$SCRIPT_DIR/data/alerts.log"

# ── helper: run a step and record its result ──
declare -a STEP_RESULTS=()
declare -a STEP_FAILED_NAMES=()
declare -a STEP_FAILED_MSGS=()

run_step() {
    local step_name="$1"
    local description="$2"
    shift 2
    local cmd=("$@")

    echo "=== [${step_name}] ${description} ===" >&2

    local start_ts
    start_ts=$(date '+%Y-%m-%dT%H:%M:%S%z')

    local exit_code=0
    "${cmd[@]}" >&2
    exit_code=$?

    local end_ts
    end_ts=$(date '+%Y-%m-%dT%H:%M:%S%z')

    if [ "$exit_code" -eq 0 ]; then
        echo "  ✓ ${step_name}: OK" >&2
    else
        echo "  ✗ ${step_name}: FAILED (exit code ${exit_code})" >&2
        echo "FAILED: ${description} — exit code ${exit_code}" >&2
        STEP_FAILED_NAMES+=("${step_name}")
        STEP_FAILED_MSGS+=("${description} — exit code ${exit_code}")
    fi

    STEP_RESULTS+=("${step_name}|${exit_code}|${description}|${start_ts}|${end_ts}")
    return 0
}

# Record a step as skipped (exit_code -1 → status "skipped" in pipeline_status.json).
# Used when an upstream failure makes running the step pointless (e.g. classifying
# stale articles after a failed scrape would burn API tokens for nothing).
skip_step() {
    local step_name="$1"
    local description="$2"
    echo "=== [${step_name}] ${description} — SKIPPED (upstream failure) ===" >&2
    local now_ts
    now_ts=$(date '+%Y-%m-%dT%H:%M:%S%z')
    STEP_RESULTS+=("${step_name}|-1|${description}|${now_ts}|${now_ts}")
}

write_status_file() {
    local overall="ok"
    if [ ${#STEP_FAILED_NAMES[@]} -gt 0 ]; then
        overall="failed"
    fi

    local json_steps="["
    local first=true
    for entry in "${STEP_RESULTS[@]}"; do
        local name desc code start end
        name="${entry%%|*}";       rest="${entry#*|}"
        code="${rest%%|*}";        rest="${rest#*|}"
        desc="${rest%%|*}";        rest="${rest#*|}"
        start="${rest%%|*}";       end="${rest##*|}"

        desc_escaped="${desc//\\/\\\\}"; desc_escaped="${desc_escaped//\"/\\\"}"

        local step_status="failed"
        if [ "$code" -eq 0 ]; then step_status="ok"
        elif [ "$code" -eq -1 ]; then step_status="skipped"; fi

        if $first; then first=false; else json_steps+=","; fi
        printf -v step_json '{"step":"%s","exit_code":%d,"description":"%s","started":"%s","finished":"%s","status":"%s"}' \
            "$name" "$code" "$desc_escaped" "$start" "$end" "$step_status"
        json_steps+="$step_json"
    done
    json_steps+="]"

    printf -v failures_json '[%s]' "$(
        local ffirst=true
        for fname in "${STEP_FAILED_NAMES[@]+"${STEP_FAILED_NAMES[@]}"}"; do
            if $ffirst; then ffirst=false; printf '"%s"' "$fname"
            else printf ',"%s"' "$fname"; fi
        done
    )"

    local now
    now=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

    cat > "$STATUS_FILE" <<STATUSEOF
{
  "pipeline_run_at": "${now}",
  "pipeline_start_local": "${PIPELINE_START}",
  "overall": "${overall}",
  "total_steps": ${#STEP_RESULTS[@]},
  "failed_count": ${#STEP_FAILED_NAMES[@]},
  "failed_steps": ${failures_json},
  "steps": ${json_steps}
}
STATUSEOF

    # Append one-line history record (parses durations in Python for portability).
    HISTORY_FILE="$SCRIPT_DIR/data/pipeline_history.jsonl"
    local first_start last_end last_idx
    if [ ${#STEP_RESULTS[@]} -gt 0 ]; then
        first_start="${STEP_RESULTS[0]}"
        # entry layout: name|exit_code|description|start_ts|end_ts
        first_start="${first_start#*|*|*|}"; first_start="${first_start%%|*}"
        last_idx=$(( ${#STEP_RESULTS[@]} - 1 ))   # macOS bash 3.2 has no negative indexing
        last_end="${STEP_RESULTS[$last_idx]}"
        last_end="${last_end##*|}"
    else
        first_start="$now"; last_end="$now"
    fi

    python3 - "$now" "$overall" "${#STEP_RESULTS[@]}" "${#STEP_FAILED_NAMES[@]}" \
                    "$failures_json" "$first_start" "$last_end" "$HISTORY_FILE" <<'PYEOF'
import json, sys
from datetime import datetime

now, overall, total, failed, failed_steps_json, start_ts, end_ts, hist_path = sys.argv[1:9]

def _parse(ts):
    # Handle the +ZZZZ form we emit
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None

start, end = _parse(start_ts), _parse(end_ts)
duration_s = int((end - start).total_seconds()) if (start and end) else None

record = {
    "run_at":       now,
    "overall":      overall,
    "total_steps":  int(total),
    "failed_count": int(failed),
    "failed_steps": json.loads(failed_steps_json),
    "duration_s":   duration_s,
}
with open(hist_path, "a", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")
PYEOF
}

mkdir -p "$SCRIPT_DIR/data"
echo "=== ${PIPELINE_START} — starting AI pipeline ===" >&2

# --resume: skip scrape/extract/split if a previous run preserved data/chunks/
RESUME=false
if [ "${1:-}" = "--resume" ]; then
    if compgen -G "$SCRIPT_DIR/data/chunks/chunk_*.json" > /dev/null; then
        RESUME=true
        echo "Resume mode: skipping scrape/extract/split (existing data/chunks/ preserved)" >&2
    else
        echo "Resume mode requested but no data/chunks/ found — falling back to full run" >&2
    fi
fi

# Downstream steps (classify→format) are gated on scrape/extract/split success:
# classifying stale articles after a failed scrape would burn API tokens for nothing.
UPSTREAM_OK=true

if ! $RESUME; then
    # Step 1: Scrape RSS/Atom feeds
    run_step "scrape" \
        "Scrape RSS/Atom feeds → raw_news.json" \
        python3 "$SCRIPT_DIR/src/scraper.py"
    [ ${#STEP_FAILED_NAMES[@]} -gt 0 ] && UPSTREAM_OK=false

    # Step 2: Extract flat article list
    # (also gated: extracting/splitting a stale raw_news.json would leave stale
    # chunks behind that a later --resume run would classify)
    if $UPSTREAM_OK; then
        run_step "extract" \
            "Extract flat article list → articles.json" \
            python3 "$SCRIPT_DIR/src/analyze_full.py" --phase extract
        [ ${#STEP_FAILED_NAMES[@]} -gt 0 ] && UPSTREAM_OK=false
    else
        skip_step "extract" "Extract flat article list → articles.json"
    fi

    # Step 3: Split into chunks (15 articles each)
    if $UPSTREAM_OK; then
        run_step "split" \
            "Split articles into chunks (15 per chunk) for AI classification" \
            python3 "$SCRIPT_DIR/src/analyze_full.py" --phase split
        [ ${#STEP_FAILED_NAMES[@]} -gt 0 ] && UPSTREAM_OK=false
    else
        skip_step "split" "Split articles into chunks (15 per chunk) for AI classification"
    fi

    if ! $UPSTREAM_OK; then
        echo "=== upstream step failed — skipping classify→format ===" >&2
    fi
fi

# Step 4: Classify all chunks in parallel via ollama-cloud API
if $UPSTREAM_OK; then
    run_step "classify" \
        "Classify chunks via ollama-cloud API (parallel)" \
        python3 "$SCRIPT_DIR/src/call_ollama.py" --all
else
    skip_step "classify" "Classify chunks via ollama-cloud API (parallel)"
fi

# Step 5: Merge classified chunks + cross-chunk dedup
if $UPSTREAM_OK; then
    run_step "merge" \
        "Merge classified chunks + cross-chunk dedup → classified.json" \
        python3 "$SCRIPT_DIR/src/merge_chunks.py"
else
    skip_step "merge" "Merge classified chunks + cross-chunk dedup → classified.json"
fi

# Step 6: Assemble digest.json + digest.md (titles only, no summaries)
if $UPSTREAM_OK; then
    run_step "assemble" \
        "Assemble final digest.json + digest.md from classified stories (titles only)" \
        python3 "$SCRIPT_DIR/src/analyze_full.py" --phase assemble
else
    skip_step "assemble" "Assemble final digest.json + digest.md from classified stories (titles only)"
fi

# Step 7: Format for Telegram — stdout
if $UPSTREAM_OK; then
    echo "=== [format] Format for Telegram → stdout ===" >&2
    FORMAT_START=$(date '+%Y-%m-%dT%H:%M:%S%z')
    FORMAT_EXIT=0
    python3 "$SCRIPT_DIR/src/format_telegram.py"
    FORMAT_EXIT=$?
    FORMAT_END=$(date '+%Y-%m-%dT%H:%M:%S%z')

    if [ "$FORMAT_EXIT" -eq 0 ]; then
        echo "  ✓ format: OK" >&2
    else
        echo "  ✗ format: FAILED (exit code ${FORMAT_EXIT})" >&2
        echo "FAILED: Format Telegram output — exit code ${FORMAT_EXIT}" >&2
        STEP_FAILED_NAMES+=("format")
        STEP_FAILED_MSGS+=("Format Telegram output — exit code ${FORMAT_EXIT}")
    fi
    STEP_RESULTS+=("format|${FORMAT_EXIT}|Format Telegram output → stdout|${FORMAT_START}|${FORMAT_END}")
else
    skip_step "format" "Format Telegram output → stdout"
fi

# Step 8: Deliver digest to Telegram (no-op unless config.telegram.enabled).
# Gated on a fully-clean run so a stale or broken digest is never sent.
DELIVER_DESC="Send digest to Telegram chat (no-op if telegram.enabled=false)"
if [ ${#STEP_FAILED_NAMES[@]} -eq 0 ]; then
    run_step "deliver" "$DELIVER_DESC" \
        python3 "$SCRIPT_DIR/src/send_telegram.py"
else
    skip_step "deliver" "$DELIVER_DESC"
fi

# Step 9: Cleanup temporary chunk files (only on a fully-clean run; otherwise preserve for --resume)
if [ ${#STEP_FAILED_NAMES[@]} -eq 0 ]; then
    run_step "cleanup" \
        "Remove temporary chunk files from chunks/" \
        python3 "$SCRIPT_DIR/src/analyze_full.py" --phase cleanup
else
    echo "=== [cleanup] skipped — pipeline had failures; data/chunks/ preserved for --resume ===" >&2
fi

# ── finalize ──
PIPELINE_END=$(date '+%Y-%m-%d %H:%M:%S MSK')
echo "=== ${PIPELINE_END} — pipeline finished ===" >&2

write_status_file

if [ ${#STEP_FAILED_NAMES[@]} -gt 0 ]; then
    echo "" >&2
    echo "⚠️  ${#STEP_FAILED_NAMES[@]} step(s) failed:" >&2
    for msg in "${STEP_FAILED_MSGS[@]}"; do
        echo "    FAILED: ${msg}" >&2
    done
    echo "Status file: ${STATUS_FILE}" >&2

    # Append one greppable line per failed run for audit / cron-mail surfacing.
    {
        printf '%s FAIL ' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        IFS=, ; printf '%s' "${STEP_FAILED_NAMES[*]}"
        printf ' — %d step(s) failed\n' "${#STEP_FAILED_NAMES[@]}"
    } >> "$ALERTS_LOG"

    exit 1
fi

exit 0
