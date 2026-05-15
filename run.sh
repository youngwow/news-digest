#!/usr/bin/env bash
# News digest AI pipeline runner
# Flow: scrape → extract → split → classify (direct API) → merge → summarize (direct API) → assemble → format
# All AI steps use direct ollama-cloud API calls (call_ollama.py / call_ollama_summarize.py).
# No regex, no hardcoded rules, no kanban dependency.
#
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
        for fname in "${STEP_FAILED_NAMES[@]}"; do
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

# ═══════════════════════════════════════════════════════
# AI Pipeline (all steps use direct ollama-cloud API)
# ═══════════════════════════════════════════════════════
echo "=== ${PIPELINE_START} — starting AI pipeline ===" >&2

# Step 1: Scrape RSS/Atom feeds
run_step "scrape" \
    "Scrape RSS/Atom feeds → raw_news.json" \
    python3 "$SCRIPT_DIR/scraper.py"

# Step 2: Extract flat article list
run_step "extract" \
    "Extract flat article list → articles.json" \
    python3 "$SCRIPT_DIR/analyze_full.py" --phase extract

# Step 3: Split into chunks (15 articles each)
run_step "split" \
    "Split articles into chunks (15 per chunk) for AI classification" \
    python3 "$SCRIPT_DIR/analyze_full.py" --phase split

# Step 4: Classify each chunk via direct ollama-cloud API (deepseek-v4-flash)
echo "" >&2
echo "=== [classify] Classify chunks via ollama-cloud API (deepseek-v4-flash) ===" >&2
CLASSIFY_START=$(date '+%Y-%m-%dT%H:%M:%S%z')
CLASSIFY_EXIT=0
for chunk in "$SCRIPT_DIR"/chunks/chunk_*.json; do
    chunk_num=$(basename "$chunk" .json | sed 's/chunk_//')
    echo "  → chunk ${chunk_num}..." >&2
    python3 "$SCRIPT_DIR/call_ollama.py" "$chunk_num" >&2 || { CLASSIFY_EXIT=1; echo "FAILED: chunk ${chunk_num} classification failed" >&2; }
done
CLASSIFY_END=$(date '+%Y-%m-%dT%H:%M:%S%z')
if [ "$CLASSIFY_EXIT" -eq 0 ]; then
    echo "  ✓ classify: OK" >&2
else
    echo "  ✗ classify: FAILED" >&2
    STEP_FAILED_NAMES+=("classify")
    STEP_FAILED_MSGS+=("AI classification of chunks — one or more chunks failed")
fi
STEP_RESULTS+=("classify|${CLASSIFY_EXIT}|Classify chunks via ollama-cloud API (deepseek-v4-flash)|${CLASSIFY_START}|${CLASSIFY_END}")

# Step 5: Merge classified chunks + cross-chunk dedup
run_step "merge" \
    "Merge classified chunks + cross-chunk dedup → classified.json" \
    python3 "$SCRIPT_DIR/merge_chunks.py"

# Step 6: Assemble digest.json + digest.md (titles only, no summaries)
run_step "assemble" \
    "Assemble final digest.json + digest.md from classified stories (titles only)" \
    python3 "$SCRIPT_DIR/analyze_full.py" --phase assemble

# Step 7: Format for Telegram — stdout
echo "=== [format] Format for Telegram → stdout ===" >&2
FORMAT_START=$(date '+%Y-%m-%dT%H:%M:%S%z')
FORMAT_EXIT=0
python3 "$SCRIPT_DIR/format_telegram.py"
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
    python3 "$SCRIPT_DIR/analyze_full.py" --phase cleanup

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
