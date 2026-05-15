#!/usr/bin/env python3
"""
Direct ollama-cloud API call for summarization.
Reads classified.json stories in chunks → sends to deepseek-v4-flash → writes summarized_chunk_N.json
"""

import json, os, sys, re
import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_URL = "https://ollama.com/v1"
MODEL = "deepseek-v4-flash"

def load_api_key():
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
    text = text.strip()
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        text = m.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text

def summarize_stories(stories, api_key):
    """Send stories to LLM, get back short_summary for each."""
    simplified = []
    for s in stories:
        simplified.append({
            "title": s["title"],
            "category": s.get("category", ""),
            "importance": s.get("importance", 5),
        })

    prompt = f"""For each of these {len(simplified)} news stories, write a short_summary in Russian (1-2 sentences, max 200 chars, factual).

Stories:
{json.dumps(simplified, ensure_ascii=False, indent=2)}

Return ONLY this JSON array:
[
  {{"index": 0, "short_summary": "краткое описание новости"}},
  ...
]

Keep the same order. Write ONLY the summary — no explanations."""

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
        return None

    result = resp.json()
    content = result["choices"][0]["message"]["content"]
    json_str = extract_json(content)

    try:
        summaries = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Content (first 500): {content[:500]}")
        return None

    return summaries


def main():
    api_key = load_api_key()
    if not api_key:
        print("ERROR: OLLAMA_API_KEY not found")
        sys.exit(1)

    # Read classified.json
    classified_path = os.path.join(HERE, "classified.json")
    with open(classified_path, encoding="utf-8") as f:
        data = json.load(f)

    all_stories = data.get("stories", [])
    total = len(all_stories)
    print(f"Total stories to summarize: {total}")

    CHUNK_SIZE = 12
    all_summarized = []

    for idx in range(0, total, CHUNK_SIZE):
        chunk = all_stories[idx:idx + CHUNK_SIZE]
        chunk_num = idx // CHUNK_SIZE + 1
        total_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE

        print(f"\n=== Chunk {chunk_num}/{total_chunks}: {len(chunk)} stories ===")

        summaries = summarize_stories(chunk, api_key)
        if summaries is None:
            print(f"  FAILED — stopping")
            break

        # Merge summaries back into stories
        for s, summary_obj in zip(chunk, summaries):
            s["short_summary"] = summary_obj.get("short_summary", "").strip()
            all_summarized.append(s)
            print(f"  ✓ {s['title'][:60]}... → {s['short_summary'][:80]}...")

    # Write summarized.json
    out_path = os.path.join(HERE, "summarized.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "summarized_at": "",
            "input_count": total,
            "stories": all_summarized,
        }, f, ensure_ascii=False, indent=2)

    print(f"\nDone: {len(all_summarized)}/{total} stories → {out_path}")


if __name__ == "__main__":
    main()
