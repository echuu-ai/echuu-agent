"""Retrieve authored mechanisms, separately from character facts and raw examples."""
import json
from pathlib import Path

def retrieve(outline,mode,store,parent,raw_examples=()):
    if mode=='none': payload={'mode':mode,'selected':[],'reason':'ablation: no references'}
    elif mode=='raw':
        payload={'mode':mode,'selected':list(raw_examples)[:2],'reason':'explicit raw reference baseline; not character evidence'}
    else:
        library=json.loads(Path(__file__).with_name('patterns.json').read_text())
        requested=set(outline.get('mechanisms',[]))
        ranked=sorted(library,key=lambda p:(-len(requested&set(p['tags'])),p['id']))
        selected=[p for p in ranked if requested&set(p['tags'])][:2]
        payload={'mode':mode,'selected':selected,'query_mechanisms':sorted(requested),
                 'reason':'match outline mechanism labels, not surface word overlap; no match gives no reference',
                 'scores':[{'id':p['id'],'matched_tags':sorted(requested&set(p['tags']))} for p in ranked]}
    aid=store.add('pattern_retrieval',payload,parents=[parent],inputs={'outline':outline,'mode':mode})
    return payload,aid
