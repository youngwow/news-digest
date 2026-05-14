#!/usr/bin/env python3
"""
Batch AI analyzer: classifies all articles, assigns importance, extracts entities,
generates structured digest — all in one pass.

Run: python3 analyze_full.py
Reads: analyzed_news.json
Writes: analyzed_news.json (with category/importance/entities/short_summary filled in)
Then: digest.json + digest.md
"""

import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYZED_FILE = os.path.join(HERE, "analyzed_news.json")
DIGEST_JSON   = os.path.join(HERE, "digest.json")
DIGEST_MD     = os.path.join(HERE, "digest.md")

# ── Category classification rules ──────────────────────────────────

CATEGORY_RULES = [
    # (regex_pattern, category) — first match wins
    (r"(всу|сво|обстрел|прекращени[ея]\s*огня|перемири|позици.\s*российск|минобороны|военн|фронт|боев|батальон|полк|бригад|штурм|контрнаступ|потер.\s*противник|уничтожен|сбит|дрон|БПЛА|беспилотник|ПВО|работает\s*артиллери|авиаудар)", "политика"),
    (r"(парад|побед[аы]|9\s*мая|день\s*побед|ветеран|бессмертн|вечный\s*огон|возложени|мемориал|памятник|ВОВ|Велик[ао]я\s*Отечествен|геро[йя]|награжден|орден|медаль|фронтов|поздрав(ил|ляют)|торжествен|празднич|акци[яи]|шествие|автопробег)", "политика"),
    (r"(Путин|Зеленский|Трамп|Байден|Си\s*Цзиньпин|Макрон|Шольц|Песков|МИД|Кремл|Белый\s*дом|госдеп|саммит|встреч[аи]\s*лидер|переговоры|визит|делегаци|посол|консул|внешн[яе]|санкци|дипломат|урегулирован|конфликт)", "политика"),
    (r"(выбор|голосован|парламент|законопроект|депутат|сенатор|конгресс|госдум|совет\s*федерац|губернатор|мэр|правительств|министр|ведомств|реформ|\bзакон\b|поправк|конституц)", "политика"),
    # Russian internal politics / law enforcement
    (r"(\bсуд\b|оштрафова[лн]|арестован|задержа[лн]|приговор[её]н|колони[ию]|уголовн|прокуратур|МВД|ФСБ\b|СК\b|следственн|экстремист|дискредитац)", "политика"),
    (r"(экономи|ВВП|инфляц|рубл[ья]|доллар|евро|юан[ья]|курс\s*валют|биржа|акци[яй]|облигац|инвестиц|\bбанк\b|центробанк|ЦБ\b|ключевая\s*ставк|кредит|ипотек|бюджет|налог|доход|расход|дефицит|профицит)", "экономика"),
    (r"(нефт[ья]|\bгаз\b|баррел|энерг|уголь|металл|золот[ао]|сырь[ея]|экспорт|импорт|торгов|поставк|сделк|контракт|тендер|рынок|цена|спрос|предложени)", "экономика"),
    (r"(искуственн.*интеллект|\bИИ\b|нейросет|GPT|ChatGPT|Gemini|Claude|Llama|машин.*обучен|\bAI\b|робот|автопилот|электромобил|Tesla|SpaceX|ракет|спутник|космическ|стартап|инноваци|\bчип\b|процессор|квантов|полупроводник)", "технологии"),
    (r"(Apple|Google|Microsoft|Amazon|\bMeta\b|NVIDIA|Intel|AMD|Samsung|Huawei|Xiaomi|смартфон|iPhone|Android|приложени|софт|\bIT\b|кибер|хакер|утечк|шифрован|блокчейн|криптовалют|битко[ий]|цифров|интернет|5G|6G)", "технологии"),
    (r"(\bспорт\b|футбол|хоккей|теннис|баскетбол|волейбол|чемпионат|турнир|олимпи|медал[ьи]|победил|проиграл|\bсч[её]т\b|\bгол\b|матч|\bигрок\b|тренер|стадион|Рублев|Медведев|Овечкин|Малкин|НХЛ|КХЛ|УЕФА|ФИФА|МОК|WADA)", "спорт"),
    (r"(наук[аи]|исследован|открыти[ея]|\bуч[её]ны[ейх]\b|лаборатор|эксперимент|\bген\b|ДНК|молекул|биолог|физик|хими[яи]|математи|астроном|телескоп|\bмарс\b|лун[аы]|NASA|Роскосмос|МКС|гравитац)", "наука"),
    (r"(культур|театр|кино|фильм|премьер|фестивал|выставк|музей|галере|концерт|музык|\bопер[аы]\b|балет|литератур|книг[аи]|писател|поэт|художник|скульптор|архитектур)", "культура"),
    (r"(Израил|Палестин|Газ[аы]|ХАМАС|Хезболл|Иран|ядерн.*программ|КСИР|Ближн.*Восток|Кита[йя]|Тайван|КНДР|Пхеньян|Коре[йя]|Инди[яи]|Пакистан|Афганистан|Таджикистан|Узбекистан|Казахстан)", "мир"),
    (r"(Сири[яи]|Йемен|Ливи[яи]|Судан|Африк|Латин.*Америк|Бразили|Мексик|Аргентин|Венесуэл|ООН|НАТО|\bЕС\b|Евросоюз|ВОЗ|ЮНЕСКО|МВФ|Всемирный\s*банк)", "мир"),
]

CATEGORY_CAT = {
    "политика": "политика",
    "экономика": "экономика",
    "технологии": "технологии",
    "мир": "мир",
    "спорт": "спорт",
    "наука": "наука",
    "культура": "культура",
}

CATEGORY_EMOJI = {
    "политика": "🏛️", "экономика": "💰", "технологии": "🤖",
    "мир": "🌍", "спорт": "⚽", "наука": "🔬", "культура": "🎭", "прочее": "📌",
}

IMPORTANCE_KEYWORDS = {
    10: [r"Путин\b", r"Трамп\b", r"ядерн", r"прекращени[ея]\s*огня", r"перемири", r"мирное\s*соглашение",
         r"встреч[аи]\s*лидер", r"саммит", r"санкци"],
    8:  [r"Зеленский", r"Кремл", r"Белый\s*дом", r"конфликт", r"урегулирован", r"обстрел",
         r"НАТО", r"\bСВО\b", r"военн", r"дипломат", r"Песков", r"МИД"],
    7:  [r"Госдум", r"правительств", r"закон", r"экономи", r"нефт", r"\bгаз\b", r"бюджет",
         r"инвестиц", r"кризис", r"катастроф", r"теракт", r"пожар\b"],
    6:  [r"\bспорт\b", r"чемпионат", r"технологи", r"наук", r"космическ"],
}

# Patterns that DISQUALIFY a category match — when these are found in context,
# the given category gets a strong score penalty (to fix first-match false positives).
# Format: (regex, category_to_penalize)
CATEGORY_DISQUALIFIERS = [
    # "премьер" alone → культура, but "премьер-министр" is политика
    (r"премьер[-\s]министр", "культура"),
    # "опер[аы]" → культура, but "операци" (military operation) should NOT match
    (r"операци", "культура"),
    # "авто" (car) in context of war/politics → not technology
    (r"\bавто\b.*(?:войн|фронт|боев|оружи|арми|иран|конфликт)", "технологии"),
]


def classify(title: str, summary: str) -> str:
    """Score-based classification with disambiguation.

    Counts pattern matches per category (not first-match-wins).
    Title-only matches get extra weight.
    Disqualifier patterns subtract from category scores.
    """
    title_lower = title.lower()
    summary_lower = summary.lower() if summary else ""
    full_text = f"{title_lower} {summary_lower}"

    # Init scores for all categories
    scores: dict[str, int] = {}
    for _, cat in CATEGORY_RULES:
        scores.setdefault(cat, 0)

    # Count matches — title matches get double weight
    for pattern, cat in CATEGORY_RULES:
        full_hits = len(re.findall(pattern, full_text))
        title_hits = len(re.findall(pattern, title_lower))
        scores[cat] += full_hits + title_hits  # title counted twice (once in full, once extra)

    # Penalize disqualified categories
    for pattern, cat_to_penalize in CATEGORY_DISQUALIFIERS:
        if re.search(pattern, full_text):
            scores[cat_to_penalize] -= 3

    if not scores:
        return "прочее"

    # Best category by score
    best_cat = max(scores, key=lambda c: scores[c])
    best_score = scores[best_cat]

    if best_score <= 0:
        return "прочее"

    # Tiebreaker: prefer category with more title-only matches
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    if len(sorted_items) >= 2 and sorted_items[1][1] == best_score:
        tied_cats = [c for c, s in scores.items() if s == best_score]
        title_scores: dict[str, int] = {}
        for pattern, cat in CATEGORY_RULES:
            title_scores[cat] = title_scores.get(cat, 0) + len(re.findall(pattern, title_lower))
        best_tied = max(tied_cats, key=lambda c: title_scores.get(c, 0))
        if title_scores.get(best_tied, 0) > 0:
            return best_tied
        return "прочее"

    return best_cat

def calc_importance(title: str, summary: str) -> int:
    text = f"{title} {summary}".lower()
    for imp, patterns in sorted(IMPORTANCE_KEYWORDS.items(), reverse=True):
        for pat in patterns:
            if re.search(pat, text):
                return imp
    return 5

def extract_entities(title: str, summary: str) -> list[str]:
    text = f"{title} {summary}"
    entities = []

    # Named entities (countries, organizations, people)
    ENTITY_PATTERNS = [
        r"(Путин[ау]?)", r"(Трамп[ау]?)", r"(Зеленск[ао]го?)", r"(Песков[ау]?)",
        r"(Кремл[ьяю])", r"(Бел\S* дом\S*)", r"(НАТО)", r"(ООН)",
        r"(Иран[ау]?)", r"(Израил[ьяю])", r"(Кита[йя]ю?)", r"(США)",
        r"(Украин[аы])", r"(Росси[ию])", r"(ФРГ)", r"(Германи[ию])",
        r"(ЕС\b)", r"(Евросоюз[ау]?)", r"(ТАСС)", r"(Минобороны)",
        r"(Казахстан[ау]?)", r"(Турци[ию])", r"(Франци[ию])",
        r"(Венесуэл[аы])", r"(КНДР)", r"(Серби[ию])", r"(ВСУ)",
        r"(ВС РФ)", r"(СВО\b)", r"(День Победы)", r"(9 мая)",
    ]
    for pat in ENTITY_PATTERNS:
        m = re.search(pat, text)
        if m:
            entities.append(m.group(1))

    # Deduplicate, keep first 5
    seen = set()
    unique = []
    for e in entities:
        el = e.lower()
        if el not in seen:
            seen.add(el)
            unique.append(e)
    return unique[:5]

def make_short_summary(title: str, summary: str) -> str:
    MAX_LEN = 180
    if summary and len(summary) > 20:
        # Strip trailing ellipsis from upstream truncation
        s = summary.rstrip("…").rstrip(".")

        # Look for a natural sentence boundary within MAX_LEN
        # Prefer: . ! ? … followed by space or end-of-string
        sentence_end = -1
        for cut in (MAX_LEN, MAX_LEN + 40):
            substr = s[:cut]
            # Scan right-to-left for best sentence boundary (sep + space)
            for sep in ("? ", "! ", ". ", "… "):
                pos = substr.rfind(sep)
                if pos > 40 and pos > sentence_end:
                    sentence_end = pos + 1  # include the punctuation
            # Also check if the cut itself lands at a sentence end
            if sentence_end < 0:
                for sep in ("?", "!", ".", "…"):
                    stripped = substr.rstrip()
                    if stripped.endswith(sep):
                        pos = len(stripped)
                        # Verify word boundary: next char must be space or EOS
                        if pos >= len(s) or (pos < len(s) and s[pos] == " "):
                            sentence_end = pos
                            break
            if sentence_end > 40:
                result = s[:sentence_end].strip()
                # Guard: if cutting at sentence boundary produces < 40 chars,
                # fall through to word-boundary fallback for a longer chunk
                if len(result) >= 40:
                    return result

        # Fallback: cut at MAX_LEN, back up to a word boundary
        truncated = s[:MAX_LEN].rstrip()
        last_space = truncated.rfind(" ")
        if last_space >= 0:
            result = truncated[:last_space] + "…"
        else:
            result = truncated + "…"

        # Minimum-length guard: if truncated result < 30 chars, fall back to title
        if len(result) >= 30:
            return result
        # Fall through to title-based fallback below

    # No summary — generate from title
    t = title.strip().rstrip(".").rstrip("…")
    if len(t) <= 150:
        return t
    # Cut at last space before 147
    cut = t[:147].rstrip()
    last_space = cut.rfind(" ")
    if last_space > 60:
        return cut[:last_space] + "…"
    return cut + "…"


# ── Main ─────────────────────────────────────────────────────────────

def main():
    with open(ANALYZED_FILE, encoding="utf-8") as f:
        data = json.load(f)

    groups = data["articles"]
    print(f"Analyzing {len(groups)} groups...")

    # Classify all
    for g in groups:
        g["category"] = classify(g["title"], g.get("summary", ""))
        g["importance"] = calc_importance(g["title"], g.get("summary", ""))
        g["entities"] = extract_entities(g["title"], g.get("summary", ""))
        g["short_summary"] = make_short_summary(g["title"], g.get("summary", ""))

    # Sort by importance desc
    groups.sort(key=lambda x: (x["importance"], x["group_size"], len(x.get("short_summary", ""))), reverse=True)

    # Save analyzed
    data["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    data["articles"] = groups
    with open(ANALYZED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved {ANALYZED_FILE}")

    # ── Build digest ──
    top = groups[:5]

    # By category
    by_cat = defaultdict(list)
    for g in groups:
        by_cat[g["category"]].append(g)

    rubrics = {}
    cat_order = ["политика", "мир", "экономика", "технологии", "спорт", "наука", "культура", "прочее"]
    for cat in cat_order:
        items = by_cat.get(cat, [])[:5]
        if items:
            rubrics[cat] = [
                {"title": g["title"], "body": g["short_summary"]} for g in items
            ]

    # Rest (brief mentions)
    rest = []
    seen_titles = set()
    for g in groups[5:]:
        t = g["title"].strip()
        if t not in seen_titles:
            rest.append(t)
            seen_titles.add(t)
    rest = rest[:50]

    headline = top[0]["title"] if top else "Нет новостей"

    # Dynamic date: gather all publish dates, use the most common day
    date_counts = Counter()
    for g in groups:
        pub = g.get("published")
        if pub:
            # ISO timestamp -> date only
            date_str_iso = pub[:10]  # "2026-05-10"
            date_counts[date_str_iso] += 1
    if date_counts:
        most_common_iso = date_counts.most_common(1)[0][0]  # "2026-05-10"
        parts = most_common_iso.split("-")
        date_str = f"{parts[2]}.{parts[1]}.{parts[0]}"  # "10.05.2026"
    else:
        date_str = datetime.now(timezone.utc).strftime("%d.%m.%Y")

    digest = {
        "headline": headline,
        "date": date_str,
        "top5": [{"title": g["title"], "body": g["short_summary"]} for g in top],
        "rubrics": rubrics,
        "rest": rest,
    }

    full = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": data["source"],
        "total_raw": data["input_count"],
        "total_analyzed": len(groups),
        "total_unique": len(groups),
        "digest": digest,
        "all_stories": groups,
    }

    with open(DIGEST_JSON, "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)
    print(f"Saved {DIGEST_JSON}")

    # ── Markdown ──
    lines = [
        f"# 📰 Дайджест новостей — {date_str}",
        "",
        f"## 🔥 Заголовок дня",
        f"**{headline}**",
        "",
        "---",
        "## 🏆 Топ-5 главных новостей",
        "",
    ]

    for i, item in enumerate(digest["top5"], 1):
        lines.append(f"### {i}. {item['title']}")
        lines.append(item["body"])
        lines.append("")

    lines += ["---", "", "## 📂 Рубрики", ""]

    for cat in cat_order:
        if cat in rubrics:
            emoji = CATEGORY_EMOJI.get(cat, "📌")
            lines.append(f"### {emoji} {cat.capitalize()}")
            lines.append("")
            for item in rubrics[cat]:
                lines.append(f"- **{item['title']}** — {item['body']}")
            lines.append("")

    lines += ["---", "", "## 📋 Остальные новости кратко", ""]
    for title in rest:
        lines.append(f"- {title}")

    lines += [
        "",
        "---",
        f"*Сгенерировано: {datetime.now().strftime('%d.%m.%Y %H:%M')} MSK*",
        f"*Всего проанализировано: {len(groups)} сюжетов из {data['input_count']} статей*",
    ]

    md = "\n".join(lines)
    with open(DIGEST_MD, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"Saved {DIGEST_MD} ({len(md)} chars)")

    # Stats
    counts = Counter(g["category"] for g in groups)
    print("\nCategory distribution:")
    for cat, cnt in counts.most_common():
        print(f"  {cat}: {cnt}")
    print(f"\nImportance distribution:")
    imp_counts = Counter(g["importance"] for g in groups)
    for imp, cnt in sorted(imp_counts.items(), reverse=True):
        print(f"  ★{imp}: {cnt}")


if __name__ == "__main__":
    main()
