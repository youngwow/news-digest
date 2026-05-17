#!/usr/bin/env python3
"""Tests for dedup_stories() in merge_chunks.py."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from merge_chunks import dedup_stories

# Stories must be in classified format (post-LLM): title, category, importance, sources, urls.
stories = [
    # Two stories about the same drone attack — enough word overlap to merge (>30%)
    {
        'title': 'Россия атаковала Украину массированным ударом дронов',
        'category': 'политика', 'importance': 8,
        'sources': ['src1'], 'urls': ['http://a.com'],
    },
    {
        'title': 'Россия атаковала Украину массированным ударом ракет и дронов',
        'category': 'политика', 'importance': 7,
        'sources': ['src2'], 'urls': ['http://b.com'],
    },
    # Two Duma stories — shorter title is substring of longer (containment merge)
    {
        'title': 'Госдума разрешила Путину привлекать военных',
        'category': 'политика', 'importance': 6,
        'sources': ['src3'], 'urls': ['http://c.com'],
    },
    {
        'title': 'Госдума разрешила Путину привлекать военных для защиты россиян',
        'category': 'политика', 'importance': 6,
        'sources': ['src4'], 'urls': ['http://d.com'],
    },
    # Standalone — no overlap with others
    {
        'title': 'Запуск новой ракеты-носителя с космодрома Восточный',
        'category': 'наука', 'importance': 5,
        'sources': ['src5'], 'urls': ['http://e.com'],
    },
]

result = dedup_stories(stories)

print(f'Input: {len(stories)}, Output: {len(result)} groups')
for s in result:
    print(f'  {s["sources"]} → {s["title"]}')
print()

assert len(result) == 3, f'Expected 3 groups, got {len(result)}'

drone_group = [s for s in result if 'src1' in s['sources'] and 'src2' in s['sources']]
duma_group  = [s for s in result if 'src3' in s['sources'] and 'src4' in s['sources']]
standalone  = [s for s in result if 'src5' in s['sources']]

assert len(drone_group) == 1, 'Expected 1 drone group'
assert len(duma_group) == 1, 'Expected 1 duma group'
assert len(standalone) == 1, 'Expected 1 standalone group'

# Canonical title is the longer one
assert drone_group[0]['title'] == 'Россия атаковала Украину массированным ударом ракет и дронов'
assert duma_group[0]['title'] == 'Госдума разрешила Путину привлекать военных для защиты россиян'

# Importance is the max of the merged stories
assert drone_group[0]['importance'] == 8
assert duma_group[0]['importance'] == 6

# Sources and urls are merged and deduplicated
assert drone_group[0]['sources'] == ['src1', 'src2']
assert drone_group[0]['urls'] == ['http://a.com', 'http://b.com']

print('ALL ASSERTIONS PASSED')
