"""Import verified completed nodes from a partial run into a fresh immutable run."""
import json
from pathlib import Path
from .store import digest

def import_checkpoint(source,store,fixture,flags,model):
    source=Path(source)
    records=[json.loads(s) for s in (source/'events.jsonl').read_text().splitlines()]
    seen=set()
    for event in records:
        aid=event['artifact_id']
        if aid!=digest({k:v for k,v in event.items() if k!='artifact_id'}):raise ValueError('checkpoint hash mismatch')
        if json.loads((source/'artifacts'/f'{aid}.json').read_text())!=event:raise ValueError('checkpoint snapshot mismatch')
        if not set(event['parent_artifact_ids'])<=seen:raise ValueError('checkpoint missing parent')
        seen.add(aid)
    config=next((e['output'] for e in records if e['node']=='run_config'),None)
    if config!={'fixture':fixture,'flags':flags.to_dict(),'model':model}:raise ValueError('checkpoint config mismatch')
    source_id=store.add('checkpoint_source',{'directory':str(source.resolve()),'events_digest':digest(records),'source_run_id':records[0]['run_id']})
    imported={}
    for e in records:
        if e['node']=='node_result' and e['status']=='accepted':
            aid=store.add('node_result',e['output'],parents=[source_id],cache_key=e['cache_key'],node_name=e.get('node_name'),source_artifact_id=e['artifact_id'])
            imported[e['cache_key']]=(e['output'],aid)
    # Prior failed requests consumed budget too; resuming cannot reset it.
    used=sum(e['node']=='provider_request' for e in records)
    return imported,used
