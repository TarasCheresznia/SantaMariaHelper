"""Restore section/topic labels and actual clause numbers after forum refresh."""
import json,re
from pathlib import Path
CLAUSE=re.compile(r"^\s*(\d+(?:\.\d+){0,5})(?=\s|[.)\-–:])")
def repair_records(records, reference_file):
    try: baseline=json.loads(Path(reference_file).read_text(encoding="utf-8"))
    except (OSError, ValueError): baseline=[]
    topics={str(x.get('topic','')):x.get('section','Общие правила') for x in baseline if isinstance(x,dict)}
    result=[]
    for row in records:
        if not isinstance(row,dict):continue
        item=dict(row)
        topic=str(item.get('topic',''))
        if topic in topics:item['section']=topics[topic]
        # Forum parser may use a chapter-level ID for all its subclauses.
        match=CLAUSE.match(str(item.get('text','')))
        if match:item['id']=match.group(1);item['rule_number']=match.group(1)
        result.append(item)
    return result
