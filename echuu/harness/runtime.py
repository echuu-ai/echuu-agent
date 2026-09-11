"""Native Show adapter and immutable runtime event sessions."""
from functools import wraps
import hashlib,uuid,time
from pathlib import Path
from .models import GateBlocked,HarnessFlags
from .production import run_production
from .store import ArtifactStore,digest

def to_state(content):
    from echuu.core.unit import Unit,Show,ScriptLine,AcousticHint
    from echuu.core.persona_model import RichPersona
    from echuu.live.state import PerformanceState,PerformerMemory
    if not content.get('release_ready'):raise GateBlocked('unapproved compiled show')
    from .delivery import DIRECTION
    expected=content['lines']
    actual=[line for unit in content['units'] for line in unit['lines']]
    if actual!=expected or len({l['id'] for l in expected})!=len(expected):raise GateBlocked('compiled line coverage mismatch')
    if any(DIRECTION.search(l['text']) for l in expected):raise GateBlocked('inline directions need delivery compilation')
    f=content['fixture'];units=[]
    for u in content['units']:
        lines=[ScriptLine(id=l['id'],text=l['text'],stage='pad') for l in u['lines'] if l['text']]
        if lines:lines[-1].stage='turn'
        units.append(Unit(index=u['index'],time_window=(u['index']*60,(u['index']+1)*60),acoustic=AcousticHint(speech_rate=u['delivery']['speech_rate']),lines=lines,purpose=u['purpose']))
    flat=[l for u in units for l in u.lines]
    if not flat:raise GateBlocked('empty compiled show')
    flat[0].stage='hook'
    persona=RichPersona(identity=f['persona'],belief='',flaw='保留人物自然的不完美',verbal_tics=())
    show=Show(persona=persona,topic=f['topic'],units=units)
    memory=PerformerMemory();memory.script_progress.update(total_lines=len(flat),current_stage='hook')
    return PerformanceState(name=f['name'],persona=f['persona'],background=f.get('background',''),topic=f['topic'],show=show,script_lines=flat,memory=memory,catchphrases=[])

def create_state(engine,name,persona,background,topic,config,seed,on_progress=None):
    from echuu.live.performer import PerformerV3
    config=config or {};fixture=dict(config.get('harness_fixture') or {})
    fixture.update(id=fixture.get('id','live'),name=name,persona=persona,background=background,topic=topic)
    fixture.setdefault('evidence',[]);fixture.setdefault('speech_rate',1.0)
    flags=HarnessFlags(**dict(config.get('harness_flags',{}),seed=seed if seed is not None else 42))
    path,manifest,content=run_production(fixture,engine.llm,config.get('harness_output','output/harness-live'),flags,on_progress=on_progress)
    engine.debug_trace['stages'].append({'id':'harness_story_v1','label':'Harness 内容生产','status':manifest['status'],'output':{'directory':str(path),'manifest':manifest}})
    if manifest['status']!='completed':raise GateBlocked('harness blocked content; inspect '+str(path))
    engine._harness_content=content;engine._harness_directory=path
    engine.performer=PerformerV3(engine.llm,engine.tts,engine.danmaku_handler,stream_lang_context=engine.stream_lang_context)
    return to_state(content)

def serializable(value):
    if isinstance(value,bytes):return {'bytes':len(value),'sha256':hashlib.sha256(value).hexdigest()}
    if isinstance(value,dict):return {str(k):serializable(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [serializable(v) for v in value]
    if value is None or isinstance(value,(str,int,float,bool)):return value
    return {'type':type(value).__name__}

def trace_runtime(method):
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        directory=getattr(self,'_harness_directory',None)
        if directory is None:
            yield from method(self,*args,**kwargs);return
        content=self._harness_content
        if not content['release_ready']:raise GateBlocked('runtime release gate')
        rid='runtime-'+uuid.uuid4().hex[:10]
        store=ArtifactStore(Path(directory)/'runtime'/rid,rid)
        from .production import MeteredLLM
        self._harness_runtime_store=store
        self._harness_runtime_llm=MeteredLLM(getattr(self,'llm',None),store,12)
        original_clients=[]
        for name in ('story_steerer','danmaku_interleaver'):
            component=getattr(self,name,None)
            if component is not None and hasattr(component,'llm'):
                original_clients.append((component,component.llm));component.llm=self._harness_runtime_llm
        parent=store.add('runtime_start',{'compiled_content_hash':digest(content),'compiled_text':content['text'],'line_ids':[l['id'] for l in content['lines']],'args':serializable(args),'kwargs':serializable(kwargs)})
        status='interrupted';count=0;begin=time.perf_counter()
        try:
            for event in method(self,*args,**kwargs):
                parent=store.add('runtime_event',serializable(event),parents=[parent],elapsed_ms=round((time.perf_counter()-begin)*1000));count+=1
                yield event
            status='completed'
        except Exception as exc:
            status='failed';store.add('runtime_failure',{'error_type':type(exc).__name__},parents=[parent],status='failed');raise
        finally:
            store.finish({'status':status,'events':count,'duration_seconds':round(time.perf_counter()-begin,2)})
            for component,original in original_clients:component.llm=original
            self._harness_runtime_store=None
            self._harness_runtime_llm=None
    return wrapped


def guard_steering(method):
    """Stage edits privately, review the entire resulting story, then commit or restore."""
    @wraps(method)
    def wrapped(self,*args,**kwargs):
        if not getattr(self,'_harness_directory',None):return method(self,*args,**kwargs)
        store=getattr(self,'_harness_runtime_store',None)
        if store is None:return '当前故事保持原稿'
        from copy import deepcopy
        from .repair import run_repair
        before=[(line,line.text) for unit in self.state.show.units for line in unit.lines]
        core=deepcopy(self.state.show.story_core)
        try:
            note=method(self,*args,**kwargs)
            proposed=[(line,line.text) for line,_ in before]
        finally:
            self.state.show.story_core=core
            for line,text in before:line.text=text
        if all(a[1]==b[1] for a,b in zip(before,proposed)):return note
        text='\n'.join(t for _,t in proposed)
        aid=store.add('runtime_steering_proposal',{'text':text,'before':[{'id':l.id,'text':t} for l,t in before]})
        try:
            _,_,review,rid=run_repair(text,self._harness_content['fixture'],self._harness_runtime_llm,store,aid,0)
            accepted=review['status']=='accepted'
        except Exception as exc:
            rid=store.add('runtime_steering_error',{'error_type':type(exc).__name__},parents=[aid],status='failed');accepted=False
        store.add('runtime_steering_selection',{'accepted':accepted,'action':'adopt' if accepted else 'restore'},parents=[aid,rid],status='accepted' if accepted else 'rejected')
        if accepted:
            for line,text in proposed:line.text=text
            return note
        return '已收到，先把当前故事讲完整'
    return wrapped


def approve_reply(engine,text):
    """Fail closed for live additions; no critic may bypass the deterministic checks."""
    if not getattr(engine,'_harness_directory',None):return True
    store=getattr(engine,'_harness_runtime_store',None)
    if store is None:return False
    from .nodes import NodeRunner,require
    from .repair import deterministic_issues,line_items
    fixture=engine._harness_content['fixture'];lines=line_items(text)
    aid=store.add('runtime_reply_proposal',{'text':text})
    if deterministic_issues(lines,fixture):
        store.add('runtime_reply_rejected',{'reason':'deterministic guard'},parents=[aid],status='rejected');return False
    def validate(d):
        require(type(d.get('pass')) is bool and isinstance(d.get('reason'),str),'invalid live verdict')
        require(isinstance(d.get('quote'),str) and bool(d['quote']) and d['quote'] in text,'invented live quote')
    try:
        nodes=NodeRunner(engine._harness_runtime_llm,store,HarnessFlags(max_calls=4))
        votes=[]
        for focus in ('人物知识/身份/相容扩写','用户输入与口语/无关总结/因果'):
            vote,_=nodes.call('runtime_reply_gate','这是简短直播回应，不是完整故事。只检查人物冲突、泄露提示或秘密、语义不通。普通相容细节允许。重点：'+focus+'。返回 {pass:bool,reason:具体理由,quote:回应中的精确连续片段}。',{'fixture':fixture,'reply':text},[aid],validate,max_tokens=700)
            votes.append(vote['pass'])
        accepted=all(votes)
    except Exception as exc:
        store.add('runtime_reply_error',{'error_type':type(exc).__name__},parents=[aid],status='failed');accepted=False
    store.add('runtime_reply_selection',{'accepted':accepted},parents=[aid],status='accepted' if accepted else 'rejected')
    return accepted
