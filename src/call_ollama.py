#!/usr/bin/env python3
"""
LLM classification via the active provider in config.yaml.
Reads chunk_N.json → classifies via LLM → writes chunk_N_classified.json.

Usage:
    call_ollama.py <N>          # classify single chunk (debugging)
    call_ollama.py --all        # discover and classify all chunks in parallel
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from threading import Lock

import httpx
from json_repair import repair_json

from utils import CONFIG, DATA_DIR, PROJECT_ROOT, extract_json, get_logger, load_env_secret

CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
CACHE_DIR  = os.path.join(DATA_DIR, "chunk_cache")
FAILED_DIR = os.path.join(DATA_DIR, "failed_chunks")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "prompts")
_llm = CONFIG["llm"]
CLASSIFY_CONCURRENCY  = _llm["classify_concurrency"]
CACHE_RETENTION_DAYS  = _llm["cache_retention_days"]


def _resolve_active_provider() -> dict:
    name = _llm["active"]
    for p in _llm["providers"]:
        if p["name"] == name:
            return p
    raise SystemExit(f"config.yaml: llm.active '{name}' not in providers")


ACTIVE = _resolve_active_provider()
PROVIDER_NAME = ACTIVE["name"]
BASE_URL      = ACTIVE["base_url"]
MODEL         = ACTIVE["model"]
MAX_RETRIES   = ACTIVE["max_retries"]
TIMEOUT       = ACTIVE["timeout"]
TEMPERATURE   = ACTIVE["temperature"]
MAX_TOKENS    = ACTIVE["max_tokens"]
API_KEY_ENV   = ACTIVE.get("api_key_env")

log = get_logger("call_ollama")


def _resolve_prompt_path() -> str:
    """Prefer prompts/classify.{provider}.txt; fall back to prompts/classify.txt."""
    override = os.path.join(PROMPTS_DIR, f"classify.{PROVIDER_NAME}.txt")
    default = os.path.join(PROMPTS_DIR, "classify.txt")
    if os.path.exists(override):
        return override
    if not os.path.exists(default):
        raise SystemExit(f"missing classification prompt: {default}")
    return default


PROMPT_PATH = _resolve_prompt_path()
with open(PROMPT_PATH, encoding="utf-8") as _f:
    PROMPT_TEMPLATE = _f.read()


def _dump_failed(chunk_num: int, raw_content: str) -> str | None:
    """Save the raw LLM response to disk so the user can inspect it later."""
    try:
        os.makedirs(FAILED_DIR, exist_ok=True)
        path = os.path.join(FAILED_DIR, f"chunk_{chunk_num}_{int(time.time())}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw_content)
        return path
    except OSError as e:
        log.warning("chunk_%d: could not save failed output: %s", chunk_num, e)
        return None


def _parse_with_repair(json_str: str, raw_content: str, chunk_num: int) -> dict | None:
    """Strict JSON parse with a json-repair fallback for sloppy LLM output."""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        log.warning("chunk_%d: invalid JSON (%s); attempting repair", chunk_num, e)

    try:
        repaired = repair_json(json_str, return_objects=False)
        parsed = json.loads(repaired)
        log.info("chunk_%d: JSON repaired successfully", chunk_num)
        return parsed
    except (ValueError, json.JSONDecodeError) as e:
        path = _dump_failed(chunk_num, raw_content)
        log.error("chunk_%d: JSON unrepairable: %s", chunk_num, e)
        if path:
            log.error("chunk_%d: full raw output saved to %s", chunk_num, path)
        else:
            log.error("chunk_%d: raw content (first 1500 chars): %s", chunk_num, raw_content[:1500])
        return None

# Cache stats are aggregated across worker threads
_cache_stats = {"hits": 0, "misses": 0}
_cache_stats_lock = Lock()


def _chunk_hash(articles: list[dict]) -> str:
    blob = json.dumps(articles, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _prune_cache() -> None:
    """Delete cached chunk classifications older than CACHE_RETENTION_DAYS."""
    if not os.path.isdir(CACHE_DIR):
        return
    cutoff = (datetime.now() - timedelta(days=CACHE_RETENTION_DAYS)).timestamp()
    removed = 0
    for entry in os.listdir(CACHE_DIR):
        path = os.path.join(CACHE_DIR, entry)
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        log.info("chunk_cache: pruned %d expired entries", removed)


def classify_chunk(chunk_num: int) -> bool:
    chunk_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}.json")
    out_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}_classified.json")

    with open(chunk_path, encoding="utf-8") as f:
        chunk_data = json.load(f)

    articles = chunk_data.get("articles", [])

    # Cache check: content-addressed, so chunk reorderings don't cause stale hits
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_key = _chunk_hash(articles)
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.json")
    if os.path.exists(cache_path):
        shutil.copy(cache_path, out_path)
        with _cache_stats_lock:
            _cache_stats["hits"] += 1
        log.info("chunk_%d: cache hit (%s), skipping API call", chunk_num, cache_key[:8])
        return True
    with _cache_stats_lock:
        _cache_stats["misses"] += 1

    headers = {"Content-Type": "application/json"}
    if API_KEY_ENV:
        api_key = load_env_secret(API_KEY_ENV)
        if not api_key:
            log.error("chunk_%d: secret '%s' not set in env or .env", chunk_num, API_KEY_ENV)
            return False
        headers["Authorization"] = f"Bearer {api_key}"

    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)
    prompt = PROMPT_TEMPLATE.format(n_articles=len(articles), articles_json=articles_json)

    log.info("chunk_%d: sending %d articles to %s (%d chars)",
             chunk_num, len(articles), MODEL, len(prompt))

    resp: httpx.Response | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.post(
                    f"{BASE_URL}/chat/completions",
                    headers=headers,
                    json={
                        "model": MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": TEMPERATURE,
                        "max_tokens": MAX_TOKENS,
                    },
                )
            if resp.status_code == 200:
                break
            log.warning("chunk_%d attempt %d/%d: HTTP %d: %s",
                        chunk_num, attempt, MAX_RETRIES, resp.status_code, resp.text[:200])
        except httpx.RequestError as e:
            log.warning("chunk_%d attempt %d/%d: request error: %s",
                        chunk_num, attempt, MAX_RETRIES, e)
            resp = None
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    else:
        log.error("chunk_%d: all %d retries exhausted", chunk_num, MAX_RETRIES)
        return False

    if resp is None or resp.status_code != 200:
        return False

    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    json_str = extract_json(content)

    parsed = _parse_with_repair(json_str, content, chunk_num)
    if parsed is None:
        return False

    stories = parsed.get("stories", [])
    output = {"chunk_size": len(articles), "stories": stories}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Populate the cache so a retry within retention skips this API call
    try:
        shutil.copy(out_path, cache_path)
    except OSError as e:
        log.warning("chunk_%d: failed to write cache entry: %s", chunk_num, e)

    log.info("chunk_%d: %d stories → %s", chunk_num, len(stories), out_path)
    return True


def classify_all() -> bool:
    """Discover chunks/chunk_N.json files and classify them in parallel."""
    _prune_cache()
    _cache_stats["hits"] = 0
    _cache_stats["misses"] = 0

    pattern = os.path.join(CHUNKS_DIR, "chunk_*.json")
    files = [f for f in glob.glob(pattern) if "_classified" not in os.path.basename(f)]
    if not files:
        log.error("no chunk files found in %s", CHUNKS_DIR)
        return False

    chunk_nums = sorted(int(os.path.basename(f).removeprefix("chunk_").removesuffix(".json"))
                        for f in files)
    log.info("classify: active provider = %s (%s, model=%s)", PROVIDER_NAME, BASE_URL, MODEL)
    log.info("classify: using prompt %s", PROMPT_PATH)
    log.info("classifying %d chunks with %d workers", len(chunk_nums), CLASSIFY_CONCURRENCY)

    failures: list[int] = []
    with ThreadPoolExecutor(max_workers=CLASSIFY_CONCURRENCY) as pool:
        futures = {pool.submit(classify_chunk, n): n for n in chunk_nums}
        for fut in as_completed(futures):
            n = futures[fut]
            try:
                ok = fut.result()
            except Exception as e:
                log.error("chunk_%d: unhandled exception: %s", n, e)
                ok = False
            if not ok:
                failures.append(n)

    log.info("chunk_cache: %d hits, %d misses", _cache_stats["hits"], _cache_stats["misses"])

    if failures:
        log.error("classify: %d/%d chunks failed: %s",
                  len(failures), len(chunk_nums), sorted(failures))
        return False
    log.info("classify: all %d chunks succeeded", len(chunk_nums))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify a news chunk via ollama-cloud LLM")
    parser.add_argument("chunk", nargs="?", help="Single chunk number (debugging mode)")
    parser.add_argument("--all", action="store_true",
                        help="Discover and classify all chunks in parallel")
    args = parser.parse_args()

    if args.all:
        return 0 if classify_all() else 1
    if args.chunk is None:
        parser.error("provide a chunk number or --all")
    return 0 if classify_chunk(int(args.chunk)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
