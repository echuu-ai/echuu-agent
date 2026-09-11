"""Bounded, observable production entry point shared by experiments and live engine."""
import json,time,uuid,hashlib,marshal
from pathlib import Path
from .models import HarnessFlags,BudgetExceeded,GateBlocked
from .store import ArtifactStore
from .nodes import NodeRunner,GateBlockedError
from .pipeline import produce,compile_content,assess_story
from .repair import line_items

class MeteredLLM:
    def __init__(self,llm,store,limit):
        self.inner,self.store,self.limit=llm,store,limit
        self.model=getattr(llm,'model','unknown');self.calls=0
    def generate(self,prompt):
        return self.call(prompt,max_tokens=2400)

    def call(self,prompt,*args,**kwargs):
        if self.calls>=self.limit:raise BudgetExceeded('all-node LLM budget exhausted')
        self.calls+=1
        rid=self.store.add('provider_request',{'prompt':prompt,'args':args,'kwargs':kwargs},model=self.model)
        begin=time.perf_counter()
        try:
            out=self.inner.call(prompt,*args,**kwargs)
        except Exception as exc:
            self.store.add('provider_response',{'error_type':type(exc).__name__},parents=[rid],status='failed');raise
        self.store.add('provider_response',{'text':out},parents=[rid],duration_ms=round((time.perf_counter()-begin)*1000))
        return out


def run_production(fixture,llm,root,flags=None,*,source=None,baseline=None,resume_from=None,on_progress=None):
    flags=flags or HarnessFlags()
    arm='legacy_v4' if baseline else ('harness_humor' if source else 'harness_'+flags.reference_mode)
    run_id=fixture['id']+'-'+arm+'-'+uuid.uuid4().hex[:8]
    store=ArtifactStore(Path(root)/run_id,run_id);store.on_progress=on_progress;begin=time.perf_counter()
    manifest={'character':fixture,'variant':arm,'flags':flags.to_dict(),'status':'failed','model':getattr(llm,'model','unknown'),'cost_usd':None,'cost_note':'Provider pricing not configured','runtime_mode':'text_experiment','seed':flags.seed}
    manifest['implementation']={'production_code_sha256':hashlib.sha256(marshal.dumps(run_production.__code__)).hexdigest(),'pipeline_code_sha256':hashlib.sha256(marshal.dumps(produce.__code__)).hexdigest(),'compiler_code_sha256':hashlib.sha256(marshal.dumps(compile_content.__code__)).hexdigest()}
    metered=MeteredLLM(llm,store,flags.max_calls);nodes=NodeRunner(metered,store,flags)
    usage=[];client=getattr(llm,'client',None);create=None
    if getattr(getattr(client,'chat',None),'completions',None):
        create=client.chat.completions.create
        def capture(*args,**kwargs):
            response=create(*args,**kwargs);u=getattr(response,'usage',None)
            usage.append(u.model_dump() if u else None);return response
        client.chat.completions.create=capture
    store.add('run_config',{'fixture':fixture,'flags':flags.to_dict(),'model':metered.model})
    rootid=store.add('raw_input',fixture,inputs=fixture)
    content=None
    try:
        if resume_from:
            from .checkpoint import import_checkpoint
            cache,used=import_checkpoint(resume_from,store,fixture,flags,metered.model)
            nodes.cache.update(cache);metered.calls=used
            manifest['resumed_from']=str(resume_from)
            manifest['prior_calls']=used
        if baseline:
            text=baseline(metered)
            (store.directory/'draft.txt').write_text(text)
            aid=store.add('legacy_baseline',{'text':text},parents=[rootid]);lines=line_items(text)
            accepted,review,gid=assess_story(nodes,fixture,text,aid)
            content={'text':text,'lines':lines,'release_ready':accepted,'preflight':{'chars':sum(len(l['text']) for l in lines),'length_matched':flags.min_chars<=sum(len(l['text']) for l in lines)<=flags.max_chars}}
            store.add('baseline_evaluation',content,parents=[gid]);manifest['gate']=review
        elif source:
            previous=json.loads((Path(source)/'run.json').read_text())
            if previous['character']!=fixture or previous['status']!='completed':raise GateBlocked('source identity or approval mismatch')
            records=[json.loads(s) for s in (Path(source)/'events.jsonl').read_text().splitlines()]
            base=next(r for r in reversed(records) if r['node']=='compiled_content')
            prior=base['output'];aid=store.add('paired_story_source',{'source_run':previous['run_id'],'artifact_id':base['artifact_id'],'content':prior},parents=[rootid])
            from .humor import humor_pass
            text,lines,hid=humor_pass(nodes,fixture,prior['text'],prior['lines'],aid,flags)
            sources=[dict(s,source_artifact_id=s['artifact_id'],artifact_id=hid) for s in prior['lineage']]
            content,_=compile_content(nodes,fixture,text,lines,hid,sources,flags)
            manifest['paired_source']=previous['run_id']
        else:content,_=produce(nodes,fixture,flags,rootid)
        (store.directory/'text.txt').write_text(content['text'])
        manifest['status']='completed' if content['release_ready'] else 'needs_review'
        manifest['preflight']=content['preflight']
    except Exception as exc:
        manifest['error_type']=type(exc).__name__
        manifest['failed_node']=nodes.last_node
        if isinstance(exc,(GateBlocked,GateBlockedError)):manifest['status']='needs_review';manifest['blocked_reason']=str(exc)
        store.add('failure',{'error_type':type(exc).__name__},parents=store.ids[-1:],status='failed')
    finally:
        if create:client.chat.completions.create=create
        manifest.update(duration_seconds=round(time.perf_counter()-begin,2),llm_call_count=metered.calls,token_usage=usage or None)
        store.finish(manifest)
    return store.directory,manifest,content
