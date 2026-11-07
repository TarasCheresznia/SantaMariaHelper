"""Quick deterministic retrieval for the Santa Maria RP local evidence corpus.
Index once at load. Rank by distinctive words and topic, not generic overlaps.
No network, UI work or text mutation. Unknown question -> no evidence.
"""
from collections import defaultdict, Counter
import math
import re
from search_engine import normalize, ALIASES

STOP = set('что такой такое как какое какая какие за на по для при если во во время можно нельзя ли это где когда сколько игрок игрока игроки наказание правило правила пункт пункты ситуация ситуации будет мне сделать есть было а и или но мне его их там'.split())

def tokens(value):
    # Strip surrounding punctuation so 'союзы.' and 'союзы' have the same index key.
    return re.findall(r'[a-zа-яё0-9]+(?:\.[0-9]+)*', normalize(value))

def stem_word(word):
    """Conservative Russian inflection normalization for lookup, not spelling correction.

    Query and corpus words share the same key; the original text is untouched.
    """
    if not any('а' <= c <= 'я' or c == 'ё' for c in word) or len(word) < 5:
        return word
    for suffix in ('иями','ями','ами','ого','ему','ыми','ими','ией','иях','иях','иях','иях',
                   'иях','иях','иях','ями','ами','иях','иях','ые','ие','ий','ый','ой',
                   'ая','яя','ую','юю','ом','ем','ах','ях','ов','ев','ей','ам','ям',
                   'ой','ей','ых','их','а','я','ы','и','е','у','ю','о'):
        if word.endswith(suffix) and len(word)-len(suffix)>=4:
            return word[:-len(suffix)]
    return word

class FastRuleIndex:
    def __init__(self, rules):
        self.rules = rules
        self.index = defaultdict(set)
        self.topic_words = []
        self.record_words = []
        for i, rule in enumerate(rules):
            topic = {stem_word(w) for w in tokens(rule.get('topic','')) if w not in STOP}
            words = {stem_word(w) for w in tokens(rule.get('text',''))} | topic
            self.topic_words.append(topic)
            self.record_words.append(words)
            for word in words:
                if len(word)>=2:self.index[word].add(i)
        self.n = len(rules)

    def find(self, question, limit=8):
        raw = [w for w in tokens(question) if w not in STOP and len(w)>=2]
        words=[]
        for word in raw:
            words.append(word)
            # Preserve explicit RP abbreviations and their meaning.
            if word in ALIASES:words.extend(tokens(ALIASES[word]))
            elif word in ('dm','db','pg','mg','rk','sk','tk','nrd'):
                words.append({'dm':'убийство','db':'наезд','pg':'боязни','mg':'информации','rk':'мести','sk':'спавне','tk':'фракции','nrd':'езды'}[word])
        words=list(dict.fromkeys(stem_word(w) for w in words if len(w)>=2 and w not in STOP))
        if not words:return []
        match = Counter()
        coverage = Counter()
        for word in words:
            ids = self.index.get(word)
            if not ids and len(word)>=5:
                # Strict prefix matching helps inflection without sweeping all rules.
                for key, values in self.index.items():
                    if len(key)>=5 and (key.startswith(word) or word.startswith(key)):
                        ids = (ids or set()) | values
            if ids:
                idf = 1 + math.log(1 + self.n/len(ids))
                for i in ids:
                    match[i] += idf * (2.8 if word in self.topic_words[i] else 1.0)
                    coverage[i] += 1
        if not match:return []
        # A two-part allegation requires evidence connecting BOTH parts.
        # Never join unrelated rules to invent a prohibition.
        # Avoid zero results for natural-language queries if relevant rules only
        # mention one concept (e.g. a topic on supplies without the word "union").
        # These are CANDIDATES, not proof of a violation.
        ranked=sorted(match,key=lambda i:(-coverage[i],-match[i],len(self.record_words[i]),i))
        return [self.rules[i] for i in ranked[:limit] if match[i] >= 1.8]
