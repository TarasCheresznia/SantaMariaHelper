"""Evidence retrieval based on model interpretation + local index.
No canned situation phrases. LLM proposes alternate search terms and topics;
local corpus remains authoritative. Results must never be treated as verified
until their actual text is checked by the answer model.
"""
from fast_retrieval import FastRuleIndex
from search_engine import normalize


def semantic_candidates(question, rules, interpretation=None, limit=9):
    if not rules:return []
    interp=interpretation if isinstance(interpretation,dict) else {}
    candidates=[];seen=set()
    index=FastRuleIndex(rules)
    searches=[question]
    for query in interp.get('search_queries',[])[:5] if isinstance(interp.get('search_queries'),list) else []:
        if isinstance(query,str) and query.strip():searches.append(query[:180])
    intent=interp.get('intent','')
    if isinstance(intent,str) and intent.strip():searches.append(intent[:180])
    for query in searches:
        for rule in index.find(query,limit=15):
            key=(rule.get('url'),rule.get('text'))
            if key not in seen:seen.add(key);candidates.append(rule)
    # Model selects from the real topic catalog; its choice is only candidate generation.
    topics=interp.get('relevant_topics',[])
    if isinstance(topics,list):
        wanted={normalize(str(t)) for t in topics[:4] if isinstance(t,str)}
        if wanted:
            for rule in rules:
                if normalize(str(rule.get('topic',''))) in wanted:
                    key=(rule.get('url'),rule.get('text'))
                    if key not in seen:seen.add(key);candidates.append(rule)
    # Preserve candidate breadth; do not mistake retrieved passages for verified facts.
    # Relevant hits from different phrasings can otherwise be hidden by the earliest query.
    ordered=[];by_topic={}
    for rule in candidates:
        topic=rule.get('topic','')
        if by_topic.get(topic,0)>=3:continue
        by_topic[topic]=by_topic.get(topic,0)+1
        ordered.append(rule)
        if len(ordered)>=limit:break
    return ordered
