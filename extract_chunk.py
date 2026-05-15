import json

with open('raw_news.json') as f:
    data = json.load(f)

articles = data['articles']
chunk = articles[37:75]  # 0-indexed: 38th to 75th article

print(f'Total articles: {len(articles)}')
print(f'Chunk size: {len(chunk)} (articles 38-75)\n')

for i, a in enumerate(chunk):
    print(f'[{37+i}] {a["source"]:15s} | {a["title"][:110]}')

with open('chunk_2.json', 'w') as f:
    json.dump({
        'chunk_id': 2,
        'article_range': '38-75',
        'articles': chunk
    }, f, ensure_ascii=False, indent=2)

print(f'\nWritten chunk_2.json with {len(chunk)} articles')
