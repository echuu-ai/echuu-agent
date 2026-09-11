"""Re-evaluate a saved failed draft with the current repair policy, in a separate run."""
import argparse,json
from pathlib import Path
from .store import ArtifactStore
from .models import HarnessFlags
from .production import MeteredLLM
from .nodes import NodeRunner
from .repair import run_repair,VERSION
from .pipeline import assess_story,compile_content

def recover(source):
    from dotenv import load_dotenv
    load_dotenv()
    from echuu.live.llm_factory import create_llm_client
    source=Path(source);manifest=json.loads((source/'run.json').read_text());fixture=manifest['character']
    events=[json.loads(s) for s in (source/'events.jsonl').read_text().splitlines()]
    draft=next(e for e in reversed(events) if e['node']=='unit_draft')['output']
    store=ArtifactStore(source/'repair-v8','repair-v8');aid=store.add('recovery_source',{'source_run':manifest['run_id'],'draft':draft})
    llm=create_llm_client();llm.seed=42;metered=MeteredLLM(llm,store,20);nodes=NodeRunner(metered,store,HarnessFlags(max_calls=20))
    result={'status':'needs_review','character':fixture,'variant':'repair_v8','repair_version':VERSION}
    try:
        text,lines,repair,rid=run_repair(draft['text'],fixture,metered,store,aid,2)
        result['repair']=repair
        ok,_,gid=assess_story(nodes,fixture,text,rid)
        if repair['status']=='accepted' and ok:
            lineage=[dict(s,source_artifact_id=s['artifact_id'],artifact_id=rid) for s in draft['lineage']]
            content,cid=compile_content(nodes,fixture,text,lines,gid,lineage,HarnessFlags())
            (store.directory/'text.txt').write_text(content['text']);result['status']='completed'
    except Exception as exc:result['error_type']=type(exc).__name__
    finally:result['llm_call_count']=metered.calls;store.finish(result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');a=p.parse_args();print(json.dumps(recover(a.source),ensure_ascii=False))
