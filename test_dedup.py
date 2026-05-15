#!/usr/bin/env python3
"""Quick test for dedup_articles semantic merging."""
import sys
sys.path.insert(0, '.')
from analyzer import dedup_articles

articles = [
    {'title': 'Россия атаковала Украину почти 900 дронами', 'summary': 'Россия запустила почти 900 дронов по Украине. Атака была массированной.', 'source': 'src1', 'url': 'http://a.com', 'published': '2026-05-14'},
    {'title': 'ВСУ сбили 900 дронов над Украиной', 'summary': 'Силы ПВО Украины сбили почти 900 беспилотников', 'source': 'src2', 'url': 'http://b.com', 'published': '2026-05-14'},
    {'title': 'Госдума разрешила Путину привлекать военных', 'summary': 'Госдума приняла закон о привлечении военных для защиты границ РФ', 'source': 'src3', 'url': 'http://c.com', 'published': '2026-05-14'},
    {'title': 'Госдума разрешила использовать армию для защиты россиян', 'summary': 'Принят закон об использовании армии для защиты россиян за рубежом', 'source': 'src4', 'url': 'http://d.com', 'published': '2026-05-14'},
    {'title': 'Россия запустила ракету в космос', 'summary': 'Успешный запуск новой ракеты-носителя с космодрома Восточный', 'source': 'src5', 'url': 'http://e.com', 'published': '2026-05-14'},
]

result = dedup_articles(articles)

print('Input: {}, Output: {} groups'.format(len(articles), len(result)))
for g in result:
    print('  [{}] group_size={} title={}'.format(g["sources"], g["group_size"], g["title"]))
print()

assert len(result) == 3, 'Expected 3 groups, got {}'.format(len(result))

drone_group = [g for g in result if len(g['sources']) == 2 and 'src1' in g['sources']]
duma_group  = [g for g in result if len(g['sources']) == 2 and 'src3' in g['sources']]
single = [g for g in result if len(g['sources']) == 1]

assert len(drone_group) == 1, 'Expected 1 drone group'
assert len(duma_group) == 1, 'Expected 1 duma group'
assert len(single) == 1, 'Expected 1 single group'

# Verify group sizes (2 original articles each)
assert drone_group[0]['group_size'] == 2
assert duma_group[0]['group_size'] == 2

# Verify canonical by longest summary
assert drone_group[0]['title'] == 'Россия атаковала Украину почти 900 дронами'
assert duma_group[0]['title'] == 'Госдума разрешила использовать армию для защиты россиян'

print('ALL ASSERTIONS PASSED')
