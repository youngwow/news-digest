#!/usr/bin/env python3
"""
Direct ollama-cloud API call for summarization.
Reads classified.json stories in chunks → sends to deepseek-v4-flash → writes summarized.json
"""

import json
import os
import sys
from datetime import datetime, timezone

import httpx

from utils import CONFIG, DATA_DIR, extract_json, load_api_key

_llm  = CONFIG["llm"]
BASE_URL = _llm["base_url"]
MODEL    = _llm["model"]
TIMEOUT  = _llm["timeout"]


def summarize_stories(stories: list[dict], api_key: str) -> list[dict] | None:
    """Send stories to LLM, get back short_summary for each."""
    simplified = [
        {"title": s["title"], "category": s.get("category", ""), "importance": s.get("importance", 5)}
        for s in stories
    ]

    prompt = f"""For each of these {len(simplified)} news stories, write a short_summary in Russian (1-2 sentences, max 200 chars, factual).

Stories:
{json.dumps(simplified, ensure_ascii=False, indent=2)}

Return ONLY this JSON array:
[
  {{"index": 0, "short_summary": "краткое описание новости"}},
  ...
]

Keep the same order. Write ONLY the summary — no explanations."""

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

    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None

    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    json_str = extract_json(content)

    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Content (first 500): {content[:500]}")
        return None


def main() -> None:
    api_key = load_api_key()
    if not api_key:
        print("ERROR: OLLAMA_API_KEY not found")
        sys.exit(1)

    classified_path = os.path.join(DATA_DIR, "classified.json")
    with open(classified_path, encoding="utf-8") as f:
        data = json.load(f)

    all_stories = data.get("stories", [])
    total = len(all_stories)
    print(f"Total stories to summarize: {total}")

    CHUNK_SIZE = CONFIG["pipeline"]["summarize_chunk_size"]
    all_summarized = []

    for idx in range(0, total, CHUNK_SIZE):
        chunk = all_stories[idx:idx + CHUNK_SIZE]
        chunk_num = idx // CHUNK_SIZE + 1
        total_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE

        print(f"\n=== Chunk {chunk_num}/{total_chunks}: {len(chunk)} stories ===")

        summaries = summarize_stories(chunk, api_key)
        if summaries is None:
            print("  FAILED — stopping")
            break

        for s, summary_obj in zip(chunk, summaries):
            s["short_summary"] = summary_obj.get("short_summary", "").strip()
            all_summarized.append(s)
            print(f"  ✓ {s['title'][:60]}... → {s['short_summary'][:80]}...")

    out_path = os.path.join(DATA_DIR, "summarized.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {"summarized_at": datetime.now(timezone.utc).isoformat(), "input_count": total, "stories": all_summarized},
            f, ensure_ascii=False, indent=2,
        )

    print(f"\nDone: {len(all_summarized)}/{total} stories → {out_path}")


if __name__ == "__main__":
    main()
