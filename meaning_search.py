"""Two-stage open-ended retrieval: model interpretation -> indexed corpus candidates.

No static situation-to-answer templates. Rule numbers, sanctions and exception details
remain source-grounded, while general conceptual answers may be ungrounded and marked.
"""
from collections import Counter, defaultdict
from fast_retrieval import FastRuleIndex, tokens
from search_engine import normalize


def number_sort(value):
    import re
    s = str(value or '')
    match = re.match(r'^\s*(\d+(?:\.\d+)*)', s)
    if not match:return (1, s.casefold())
    return (0,tuple(int(x) for x in match.group(1).split('.')),s.casefold())


def topic_catalog(rules):
    return list(dict.fromkeys(str(r.get('topic','')) for r in rules if r.get('topic')))


def find_evidence(question, rules, interpretation=None, index=None, limit=18):
    if not rules:return []
    idx = index or FastRuleIndex(rules)
    info = interpretation if isinstance(interpretation,dict) else {}
    queries=[question]
    for value in info.get('search_queries',[]) if isinstance(info.get('search_queries'),list) else []:
        if isinstance(value,str) and value.strip():queries.append(value.strip()[:220])
    if isinstance(info.get('intent'),str) and info['intent'].strip():queries.append(info['intent'][:250])
    # Include distinctive individual tokens: short source clauses (e.g. 'Запрещены IC союзы')
    # need not contain the whole user's story and would otherwise lose to long topic matches.
    generic={'можно','нельзя','какие','какое','почему','когда','будет','игрок','игрока','если','правила','правило','хочу','узнать','нарушение'}
    for word in tokens(question):
        if len(word)>=4 and word not in generic:queries.append(word)
    # Score across all reformulations instead of arbitrarily keeping only the first list.
    scores=Counter();by_key={};seen_queries=set()
    for q in queries[:9]:
        qn=normalize(q)
        if qn in seen_queries:continue
        seen_queries.add(qn)
        for rank,r in enumerate(idx.find(q,limit=30)):
            key=(r.get('url'),r.get('text'))
            by_key[key]=r
            scores[key]+=(30-rank)/30*(0.65 if len(q.split())==1 else 1.0)
    exact_topics=set(normalize(t) for t in info.get('relevant_topics',[])[:5] if isinstance(t,str))
    if exact_topics:
        for r in rules:
            if normalize(r.get('topic','')) in exact_topics:
                key=(r.get('url'),r.get('text'))
                by_key[key]=r
                scores[key]+=0.28
    # Re-rank inside model-selected topics; a short but critical point can be lost
    # in global search when unrelated events also mention the same term.
    if exact_topics:
        focused=[r for r in rules if normalize(r.get('topic','')) in exact_topics]
        if focused:
            focused_index=FastRuleIndex(focused)
            for q in queries[:12]:
                for rank,r in enumerate(focused_index.find(q,limit=20)):
                    key=(r.get('url'),r.get('text'))
                    by_key[key]=r
                    scores[key]+=(20-rank)/20 * (4.5 if len(q.split())==1 else 2.3)
    # Give a small boost to direct question matches.
    direct={(r.get('url'),r.get('text')) for r in idx.find(question,limit=14)}
    for key in direct:scores[key]+=0.25
    ranked=sorted(scores, key=lambda k:(-scores[k],number_sort(by_key[k].get('rule_number') or by_key[k].get('id'))))
    selected=[];counts=defaultdict(int)
    for k in ranked:
        rule=by_key[k]; t=rule.get('topic','')
        if counts[t]>=10:continue
        counts[t]+=1;selected.append(rule)
        if len(selected)>=limit:break
    return selected
