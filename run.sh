#!/usr/bin/env bash
# News digest pipeline runner
# Full pipeline: scrape → dedup → analyze → format → output to stdout
# Usage: ./run.sh
# Output: compact Telegram-friendly digest on stdout
#
# Exit codes:
#   0 — all steps passed
#   1 — one or more steps failed
#
# Writes pipeline_status.json with per-step results for monitoring.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PIPELINE_START=$(date '+%Y-%m-%d %H:%M:%S MSK')
STATUS_FILE="$SCRIPT_DIR/pipeline_status.json"

# ── helper: run a step and record its result ──
# Usage: run_step <step_name> <description> <command...>
# Returns: 0 on success, 1 on failure (but doesn't stop the script)
# Records result in STEP_RESULTS array as "name|exit_code|description"
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

    # Run the command, capture exit code
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

    return 0  # never stop the pipeline — collect all results
}

# ── write pipeline_status.json ──
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

        # escape desc for JSON
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
        for fname in "${STEP_FAILED_NAMES[@]}"; do
            if $ffirst; then
                ffirst=false
                printf '"%s"' "$fname"
            else
                printf ',"%s"' "$fname"
            fi
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

# ── pipeline ──
echo "=== ${PIPELINE_START} — starting pipeline ===" >&2

# Step 1: Scrape
run_step "scrape" \
    "Scrape RSS/Atom feeds → raw_news.json" \
    python3 "$SCRIPT_DIR/scraper.py"

# Step 2: Dedup
run_step "dedup" \
    "Deduplicate articles → analyzed_news.json" \
    python3 "$SCRIPT_DIR/analyzer.py" --phase dedup

# Step 3: Analyze
run_step "analyze" \
    "Analyze (classify, score, entities) → digest.json + digest.md" \
    python3 "$SCRIPT_DIR/analyze_full.py"

# Step 4: Format — this one goes to stdout for the caller
echo "=== [format] Format for Telegram → stdout ===" >&2
FORMAT_START=$(date '+%Y-%m-%dT%H:%M:%S%z')
FORMAT_EXIT=0
python3 "$SCRIPT_DIR/format_telegram.py"
FORMAT_EXIT=$?
FORMAT_END=$(date '+%Y-%m-%dT%H:%M:%S%z')

# Record format step manually (output went to stdout, not stderr)
if [ "$FORMAT_EXIT" -eq 0 ]; then
    echo "  ✓ format: OK" >&2
else
    echo "  ✗ format: FAILED (exit code ${FORMAT_EXIT})" >&2
    echo "FAILED: Format Telegram output — exit code ${FORMAT_EXIT}" >&2
    STEP_FAILED_NAMES+=("format")
    STEP_FAILED_MSGS+=("Format Telegram output — exit code ${FORMAT_EXIT}")
fi
STEP_RESULTS+=("format|${FORMAT_EXIT}|Format Telegram output → stdout|${FORMAT_START}|${FORMAT_END}")

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
    exit 1
fi

exit 0
