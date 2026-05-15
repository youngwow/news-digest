#!/usr/bin/env python3
"""
Direct ollama-cloud API call via httpx.
Reads chunk_N.json → sends to deepseek-v4-flash → writes chunk_N_classified.json
"""

import json, os, sys, re
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(HERE, "chunks")
BASE_URL = "https://ollama.com/v1"
MODEL = "deepseek-v4-flash"

def load_api_key():
    """Load OLLAMA_API_KEY from .env or environment."""
    key = os.environ.get("OLLAMA_API_KEY", "")
    if key:
        return key
    env_path = os.path.join(HERE, ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("OLLAMA_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    return ""

def extract_json(text):
    """Extract JSON from LLM response (may have markdown wrapping)."""
    text = text.strip()
    # Strip markdown code fences
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    # Find first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return text

def classify_chunk(chunk_num):
    chunk_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}.json")
    out_path = os.path.join(CHUNKS_DIR, f"chunk_{chunk_num}_classified.json")

    api_key = load_api_key()
    if not api_key:
        print(f"ERROR: OLLAMA_API_KEY not found")
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

    with httpx.Client(timeout=180) as client:
        resp = client.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 16384,
            },
        )

    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}: {resp.text[:300]}")
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
