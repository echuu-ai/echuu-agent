"""Replay only delivery compilation; preserve the original experiment and scores."""
import argparse,json
from pathlib import Path
from .production import MeteredLLM
from .models import HarnessFlags
from .nodes import NodeRunner
from .pipeline import compile_content
from .store import ArtifactStore

def recompile(source):
    source=Path(source);manifest=json.loads((source/'run.json').read_text())
    if manifest['status']!='completed':raise ValueError('source not approved')
    records=[json.loads(s) for s in (source/'events.jsonl').read_text().splitlines()]
    prior=next(e for e in reversed(records) if e['node']=='compiled_content');content=prior['output']
    from dotenv import load_dotenv
    load_dotenv()
    from echuu.live.llm_factory import create_llm_client
    llm=create_llm_client();llm.seed=42
    rid='delivery-v2';store=ArtifactStore(source/rid,rid)
    aid=store.add('recompile_source',{'source_run':manifest['run_id'],'source_artifact':prior['artifact_id'],'content':content})
    nodes=NodeRunner(MeteredLLM(llm,store,12),store,HarnessFlags(max_calls=12))
    result={'character':content['fixture'],'status':'failed','variant':'delivery_v2'}
    try:
        sources=[dict(s,source_artifact_id=s['artifact_id'],artifact_id=aid) for s in content['lineage']]
        new,cid=compile_content(nodes,content['fixture'],content['text'],content['lines'],aid,sources,HarnessFlags())
        (store.directory/'text.txt').write_text(new['text']);result['status']='completed';result['compiled_id']=cid
    except Exception as exc:result['error_type']=type(exc).__name__
    finally:store.finish(result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');a=p.parse_args();print(json.dumps(recompile(a.source),ensure_ascii=False))
