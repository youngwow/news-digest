#!/usr/bin/env python3
"""
Mechanical chunk merger with aggressive cross-chunk dedup.
Reads chunk_N_classified.json from chunks/ → deduplicates within categories → writes classified.json.
"""
import json, os, sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(HERE, "chunks")

# ── helpers ──

def title_words(title):
    """Extract significant words from title (no regex)."""
    t = title.lower()
    for ch in ".,!?;:—«»\"'()[]{}…":
        t = t.replace(ch, " ")
    words = t.split()
    stop = {"и","в","во","не","что","он","на","я","с","со","как","а","то","все",
            "она","так","но","его","по","из","у","же","за","от","о","бы","для",
            "это","или","до","мы","их","был","еще","к","когда","да","вы","при",
            "без","под","нет","ли","там","где","уже","если","быть","себя","этот",
            "будет","после","может","теперь","сейчас","через","пока","них","здесь",
            "также","только","сообщил","заявил","рассказал","пишет","написал",
            "новый","стало","известно","очень"}
    return {w for w in words if len(w) > 2 and w not in stop}


def merge_stories(s1, s2):
    """Merge s2 into s1 (in-place)."""
    s1["sources"] = sorted(set(s1.get("sources", []) + s2.get("sources", [])))
    s1["urls"] = list(dict.fromkeys(s1.get("urls", []) + s2.get("urls", [])))
    s1["importance"] = max(s1.get("importance", 5), s2.get("importance", 5))
    if len(s2.get("title", "")) > len(s1.get("title", "")):
        s1["title"] = s2["title"]
    if len(s2.get("short_summary", "")) > len(s1.get("short_summary", "")):
        s1["short_summary"] = s2["short_summary"]
    return s1


def dedup_stories(stories):
    """
    Aggressive cross-chunk dedup within a flat list.
    Groups stories that mention the same event (paraphrased titles).
    """
    n = len(stories)
    words_sets = [title_words(s["title"]) for s in stories]
    used = [False] * n
    merged = []
    
    for i in range(n):
        if used[i]:
            continue
        story = dict(stories[i])
        wi = words_sets[i]
        ti = story["title"].lower().rstrip(".")
        
        for j in range(i + 1, n):
            if used[j]:
                continue
            wj = words_sets[j]
            tj = stories[j]["title"].lower().rstrip(".")
            
            if not wi or not wj:
                continue
            
            shared = wi & wj
            total = wi | wj
            overlap = len(shared) / len(total) if total else 0
            
            # Aggressive merge conditions:
            # 1. >30% word overlap
            # 2. >=3 shared significant words
            # 3. One title fully contains the other (>15 chars)
            # 4. >=2 shared words AND one is a key entity (country, person, org)
            if (overlap > 0.30 or len(shared) >= 3
                or (len(ti) > 15 and ti in tj) or (len(tj) > 15 and tj in ti)):
                story = merge_stories(story, stories[j])
                used[j] = True
        
        merged.append(story)
    
    return merged


# ── main ──

all_stories = []
for i in range(1, 20):
    path = os.path.join(CHUNKS_DIR, f"chunk_{i}_classified.json")
    input_path = os.path.join(CHUNKS_DIR, f"chunk_{i}.json")
    # Skip chunks without input (no more chunks) or without classified (API failure)
    if not os.path.exists(input_path):
        break
    if not os.path.exists(path):
        print(f"  chunk_{i}: SKIPPED (classified not found — API failure)")
        continue
    with open(path) as f:
        data = json.load(f)
    all_stories.extend(data.get("stories", []))
    print(f"  chunk_{i}: {len(data.get('stories',[]))} stories")

raw_count = len(all_stories)
print(f"\nRaw merge: {raw_count} stories")

# Aggressive dedup
deduped = dedup_stories(all_stories)
print(f"After dedup: {len(deduped)} stories (removed {raw_count - len(deduped)} duplicates)")

# Write classified.json
out_path = os.path.join(HERE, "classified.json")
with open(out_path, "w") as f:
    json.dump({
        "classified_at": datetime.now(timezone.utc).isoformat(),
        "input_count": raw_count,
        "stories": deduped,
    }, f, ensure_ascii=False, indent=2)

print(f"Wrote {len(deduped)} stories → {out_path}")
