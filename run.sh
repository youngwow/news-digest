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

        if $first; then first=false; else json_steps+=","; fi
        printf -v step_json '{"step":"%s","exit_code":%d,"description":"%s","started":"%s","finished":"%s","status":"%s"}' \
            "$name" "$code" "$desc_escaped" "$start" "$end" \
            "$([ "$code" -eq 0 ] && echo "ok" || echo "failed")"
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
}

mkdir -p "$SCRIPT_DIR/data"
echo "=== ${PIPELINE_START} — starting AI pipeline ===" >&2

# Step 1: Scrape RSS/Atom feeds
run_step "scrape" \
    "Scrape RSS/Atom feeds → raw_news.json" \
    python3 "$SCRIPT_DIR/src/scraper.py"

# Step 2: Extract flat article list
run_step "extract" \
    "Extract flat article list → articles.json" \
    python3 "$SCRIPT_DIR/src/analyze_full.py" --phase extract

# Step 3: Split into chunks (15 articles each)
run_step "split" \
    "Split articles into chunks (15 per chunk) for AI classification" \
    python3 "$SCRIPT_DIR/src/analyze_full.py" --phase split

# Step 4: Classify all chunks in parallel via ollama-cloud API
run_step "classify" \
    "Classify chunks via ollama-cloud API (parallel)" \
    python3 "$SCRIPT_DIR/src/call_ollama.py" --all

# Step 5: Merge classified chunks + cross-chunk dedup
run_step "merge" \
    "Merge classified chunks + cross-chunk dedup → classified.json" \
    python3 "$SCRIPT_DIR/src/merge_chunks.py"

# Step 6: Assemble digest.json + digest.md (titles only, no summaries)
run_step "assemble" \
    "Assemble final digest.json + digest.md from classified stories (titles only)" \
    python3 "$SCRIPT_DIR/src/analyze_full.py" --phase assemble

# Step 7: Format for Telegram — stdout
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

# Step 8: Cleanup temporary chunk files
run_step "cleanup" \
    "Remove temporary chunk files from chunks/" \
    python3 "$SCRIPT_DIR/src/analyze_full.py" --phase cleanup

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
