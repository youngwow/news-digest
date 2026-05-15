#!/usr/bin/env python3
"""
AI classifier — direct ollama-cloud API call, no subagent overhead.
Reads chunk_N.json, sends articles to LLM for classification/dedup,
writes chunk_N_classified.json.

Usage: python3 ai_classify.py <chunk_number>
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://ollama.com/v1"
MODEL = "deepseek-v4-flash"

CATEGORIES = ["политика", "экономика", "технологии", "мир", "спорт", "наука", "культура", "прочее"]

SYSTEM_PROMPT = """You are a news classifier. For each article, assign a category from: политика, экономика, технологии, мир, спорт, наука, культура, прочее.
Then deduplicate: merge articles about the SAME EVENT into stories (paraphrased titles about same topic = same story).
Assign importance 1-10 per story.
Output ONLY valid JSON, no explanation."""

def classify_chunk(chunk_num: int):
    chunk_path = os.path.join(HERE, f"chunk_{chunk_num}.json")
    out_path = os.path.join(HERE, f"chunk_{chunk_num}_classified.json")

    # Read API key: 1) env var, 2) .env in project dir, 3) ~/.hermes/.env
    api_key = os.environ.get("OLLAMA_API_KEY", "")
    if not api_key:
        for env_path in [
            os.path.join(HERE, ".env"),
            os.path.expanduser("~/.hermes/.env"),
        ]:
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OLLAMA_API_KEY="):
                            val = line.split("=", 1)[1].strip()
                            if val:
                                api_key = val
                                break
            if api_key:
                break

    if not api_key:
        print("ERROR: OLLAMA_API_KEY not found in env, .env, or ~/.hermes/.env")
        sys.exit(1)

    with open(chunk_path, encoding="utf-8") as f:
        chunk_data = json.load(f)

    articles = chunk_data.get("articles", [])
    articles_text = json.dumps(articles, ensure_ascii=False, indent=2)

    user_prompt = f"""Classify these {len(articles)} news articles. Categories: {', '.join(CATEGORIES)}.
Deduplicate: merge articles about the SAME EVENT into ONE story.
Assign importance 1-10 per story.

Articles:
{articles_text}

Return ONLY this JSON:
{{"stories": [{{"title": "best title from group", "category": "one of the categories", "importance": 1-10, "sources": ["src1"], "urls": ["url1"]}}]}}"""

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }

    for attempt in range(3):
        try:
            req = urllib.request.Request(
                f"{BASE_URL}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"]

                # Extract JSON from response (may have markdown wrapping)
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]
                content = content.strip()

                parsed = json.loads(content)
                stories = parsed.get("stories", [])

                output = {
                    "chunk_size": len(articles),
                    "stories": stories,
                }
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(output, f, ensure_ascii=False, indent=2)
                print(f"Chunk {chunk_num}: {len(stories)} stories → {out_path}")
                return out_path

        except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"  Attempt {attempt+1} failed: {e}")
            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    print(f"  FAILED after 3 attempts")
    sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 ai_classify.py <chunk_number>")
        sys.exit(1)
    classify_chunk(int(sys.argv[1]))
