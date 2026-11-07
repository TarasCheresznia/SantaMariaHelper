"""Conservative spelling recovery for Russian RP queries using the local rules corpus.

Only helps retrieval: it does not change the user's submitted question and cannot
create new evidence, rule numbers or penalties.
"""
import re
from collections import Counter
from difflib import get_close_matches
from functools import lru_cache
from search_engine import normalize

COMMON = ['что', 'такое', 'когда', 'если', 'игрок', 'игрока', 'игроков', 'его', 'меня', 'тебе', 'можно', 'нельзя', 'какое', 'наказание', 'за', 'сколько', 'дают', 'какой', 'какая', 'какие', 'будет', 'ли', 'как', 'почему', 'после', 'до', 'на', 'в', 'по', 'для', 'от', 'с', 'и', 'или', 'это', 'там', 'тут', 'он', 'она', 'они', 'мне', 'при', 'было', 'были', 'кто', 'где', 'можно', 'ли', 'правило', 'правила', 'пункт', 'пункта', 'вопрос', 'ответ', 'скажи', 'расскажи', 'объясни', 'помоги', 'привет', 'здравствуйте', 'спасибо', 'пожалуйста', 'надо', 'нужно', 'сделать', 'делает', 'сделал', 'запрещено', 'нарушение', 'нарушил', 'нарушает', 'нарушение', 'нарушений', 'действия', 'ситуации', 'случай', 'касается', 'происходит', 'произошло', 'происходит', 'считать', 'считается', 'админ', 'администрация', 'администратор']

@lru_cache(maxsize=2)
def vocabulary(texts):
    counts = Counter()
    for text in texts:
        for raw in normalize(text).split():
            word = raw.strip('.')
            if len(word) >= 4 and not word.isdigit() and not re.fullmatch(r"[\d.]+", word):
                counts[word] += 1
    for word in COMMON:
        counts[word] += 30
    return counts

def repair_query(query, rules, commands=()):
    """Return (normalized query, [(original, correction)]); conservative replacements."""
    corpus = tuple(str(x.get('text',''))[:1200] for x in rules)
    corpus += tuple(str(x.get('description','')) for x in commands)
    words = vocabulary(corpus)
    tokens = normalize(query).split()
    corrected = []
    edits = []
    for token in tokens:
        if len(token) < 5 or token in words or token.isdigit() or re.fullmatch(r'\d+(?:\.\d+)+',token):
            corrected.append(token); continue
        matches = get_close_matches(token, words.keys(), n=3, cutoff=0.84)
        if not matches:
            corrected.append(token); continue
        # Avoid speculative corrections: require a clear best candidate.
        from difflib import SequenceMatcher
        ranked = sorted(((SequenceMatcher(None,token,w).ratio(),w) for w in matches),reverse=True)
        if len(ranked)>1 and ranked[0][0]-ranked[1][0]<0.055:
            corrected.append(token); continue
        winner = ranked[0][1]
        if abs(len(token)-len(winner))>2:
            corrected.append(token); continue
        corrected.append(winner)
        edits.append((token,winner))
    return ' '.join(corrected), edits
