"""Локальный поиск по пунктам: фразы, частичные цитаты, опечатки."""
import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache

ALIASES = {
    'дм': 'dm убийство без причины', 'дб': 'db наезд транспорт',
    'пг': 'pg отсутствие боязни за жизнь', 'мг': 'mg метагейминг ooc ic',
    'рк': 'rk nlr месть после смерти', 'ск': 'sk убийство на спавне',
    'тк': 'tk убийство своих', 'зз': 'зеленая зона',
    'нрд': 'nrd езда вне дороги', 'афк': 'afk отошел',
    'читы': 'программы скрипты преимущество',
    'мат': 'нецензурные слова оскорбления',
    'репорт': 'report администрация',
}


def normalize(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().replace('ё', 'е')
    return re.sub(r'[^\w.]+', ' ', value).strip()


def _ratio(a, b):
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


@lru_cache(maxsize=8192)
def _word_score(query_word, text_word):
    if query_word == text_word:
        return 1.0
    if len(query_word) >= 2 and text_word.startswith(query_word):
        return 0.93
    if len(query_word) >= 4 and query_word in text_word:
        return 0.85
    if len(query_word) >= 4 and len(text_word) >= 4:
        similarity = _ratio(query_word, text_word)
        if similarity >= 0.73:
            return similarity * 0.82
    return 0.0


def _phrase_score(query_tokens, text_tokens):
    """Best nearby text passage of roughly the same length as a fragment."""
    if not query_tokens or not text_tokens:
        return 0.0
    q = ' '.join(query_tokens)
    n = len(query_tokens)
    best = 0.0
    # Allows a few missing/extra words but penalizes very different length.
    for window_size in range(max(1, n - 2), min(len(text_tokens), n + 3) + 1):
        for offset in range(len(text_tokens) - window_size + 1):
            passage = ' '.join(text_tokens[offset:offset + window_size])
            match = _ratio(q, passage)
            if match > best:
                best = match
    return best


def relevance(item, query):
    q = normalize(query)
    if not q:
        return 1.0
    rule_id = normalize(item['id'])
    if q == rule_id:
        return 1000.0
    text = normalize(item['text'])
    keywords = normalize(item.get('keywords', ''))
    if (q in text.split() if len(q) < 4 else q in text):
        return 300.0 + min(len(q), 100)
    if (q in keywords.split() if len(q) < 4 else q in keywords) or (q in rule_id and len(q) >= 2):
        return 150.0
    tokens = q.split()
    body_tokens = text.split()
    searchable_tokens = body_tokens + keywords.split() + rule_id.split()
    if not searchable_tokens:
        return 0.0

    expanded = normalize(ALIASES.get(q, ''))
    if expanded:
        alias_words = expanded.split()
        alias_scores = [max((_word_score(w, t) for t in searchable_tokens), default=0)
                        for w in alias_words]
        alias_bonus = 35 * (sum(alias_scores) / len(alias_scores)) if alias_scores else 0
    else:
        alias_bonus = 0

    word_scores = [max((_word_score(w, t) for t in searchable_tokens), default=0)
                   for w in tokens]
    coverage = sum(word_scores) / len(word_scores)
    found_ratio = sum(score >= .6 for score in word_scores) / len(word_scores)
    passage = _phrase_score(tokens, body_tokens[:450]) if len(q) >= 5 and (coverage >= .30 or len(tokens) == 1) else 0.0

    # Minimum evidence to avoid displaying nearly every rule for short prompts.
    if max(coverage, passage) < .43 and not alias_bonus:
        return 0.0
    if len(tokens) > 2 and found_ratio < .34 and passage < .70 and not alias_bonus:
        return 0.0
    return round(coverage * 95 + found_ratio * 40 + passage * 115 + alias_bonus, 3)


def search_rules(rules, query, limit=35):
    ranked = sorted(((relevance(item, query), item) for item in rules),
                    key=lambda entry: (-entry[0], entry[1]['id']))
    return [(score, item) for score, item in ranked if score > 0][:limit]
