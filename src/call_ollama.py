#!/usr/bin/env python3
"""
Direct ollama-cloud API call via httpx.
Reads chunk_N.json → sends to deepseek-v4-flash → writes chunk_N_classified.json
"""

import json
import os
import sys
import time

import httpx

from utils import CONFIG, DATA_DIR, extract_json, load_api_key

CHUNKS_DIR = os.path.join(DATA_DIR, "chunks")
_llm = CONFIG["llm"]
BASE_URL    = _llm["base_url"]
MODEL       = _llm["model"]
MAX_RETRIES = _llm["max_retries"]
TIMEOUT     = _llm["timeout"]


def classify_chunk(chunk_num: int) -> bool:
    chunk_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}.json")
    out_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}_classified.json")

    api_key = load_api_key()
    if not api_key:
        print("ERROR: OLLAMA_API_KEY not found")
        return False

    with open(chunk_path, encoding="utf-8") as f:
        chunk_data = json.load(f)

    articles = chunk_data.get("articles", [])
    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)

    prompt = f"""Classify these {len(articles)} Russian news articles.
Categories: политика, экономика, технологии, мир, спорт, наука, культура, прочее.
Deduplicate: merge articles about the SAME EVENT into one story.
Assign importance 1-10 per story.

Articles:
{articles_json}

Return ONLY valid JSON:
{{"stories":[{{"title":"best title","category":"cat","importance":1-10,"sources":["src"],"urls":["url"]}}]}}"""

    print(f"Sending {len(articles)} articles to {MODEL}... ({len(prompt)} chars)")

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
            print(f"Attempt {attempt}/{MAX_RETRIES}: HTTP {resp.status_code}: {resp.text[:200]}")
        except httpx.RequestError as e:
            print(f"Attempt {attempt}/{MAX_RETRIES}: request error: {e}")
            resp = None
        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)
    else:
        print("All retries exhausted")
        return False

    if resp is None or resp.status_code != 200:
        return False

    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    json_str = extract_json(content)

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw content: {content[:500]}")
        return False

    stories = parsed.get("stories", [])
    output = {"chunk_size": len(articles), "stories": stories}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"  ✓ {len(stories)} stories → {out_path}")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 call_ollama.py <chunk_number>")
        sys.exit(1)
    ok = classify_chunk(int(sys.argv[1]))
    sys.exit(0 if ok else 1)
