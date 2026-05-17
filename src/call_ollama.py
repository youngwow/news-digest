#!/usr/bin/env python3
"""
Direct ollama-cloud API call via httpx.
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

from utils import CONFIG, DATA_DIR, extract_json, get_logger, load_api_key

CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
CACHE_DIR  = os.path.join(DATA_DIR, "chunk_cache")
_llm = CONFIG["llm"]
BASE_URL              = _llm["base_url"]
MODEL                 = _llm["model"]
MAX_RETRIES           = _llm["max_retries"]
TIMEOUT               = _llm["timeout"]
CLASSIFY_CONCURRENCY  = _llm["classify_concurrency"]
CACHE_RETENTION_DAYS  = _llm["cache_retention_days"]

log = get_logger("call_ollama")

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

    api_key = load_api_key()
    if not api_key:
        log.error("chunk_%d: OLLAMA_API_KEY not found", chunk_num)
        return False

    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)

    prompt = f"""Classify these {len(articles)} Russian news articles.
Categories: политика, экономика, технологии, мир, спорт, наука, культура, прочее.
Deduplicate: merge articles about the SAME EVENT into one story.
Assign importance 1-10 per story.

Articles:
{articles_json}

Return ONLY valid JSON:
{{"stories":[{{"title":"best title","category":"cat","importance":1-10,"sources":["src"],"urls":["url"]}}]}}"""

    log.info("chunk_%d: sending %d articles to %s (%d chars)",
             chunk_num, len(articles), MODEL, len(prompt))

    resp: httpx.Response | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    json={
                        "model": MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": _llm["temperature"],
                        "max_tokens": _llm["max_tokens"],
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

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.error("chunk_%d: JSON parse error: %s", chunk_num, e)
        log.error("chunk_%d: raw content (first 500 chars): %s", chunk_num, content[:500])
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
