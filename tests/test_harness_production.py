import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from echuu.harness.models import HarnessFlags,BudgetExceeded,GateBlocked
from echuu.harness.nodes import NodeRunner
from echuu.harness.store import ArtifactStore
from echuu.harness.production import MeteredLLM
from echuu.harness.runtime import to_state,trace_runtime
from echuu.harness.humor import humor_pass
from echuu.harness.pipeline import compile_content

class Fake:
    model='test'
    def __init__(self,values):self.values=iter(values);self.calls=0
    def call(self,*a,**kw):self.calls+=1;return json.dumps(next(self.values))

def test_invalid_node_retries_and_cache_revalidates(tmp_path):
    store=ArtifactStore(tmp_path/'r','r');llm=Fake([{'x':0},{'x':1}]);nodes=NodeRunner(llm,store,HarnessFlags())
    def validate(d):
        if d['x']!=1:raise ValueError('bad x')
    one,aid=nodes.call('test','instruction',{},[],validate)
    restored=NodeRunner(llm,store,HarnessFlags())
    two,bid=restored.call('test','instruction',{},[],validate)
    assert one==two and llm.calls==2 and aid!=bid

def test_global_budget_counts_all_consumers(tmp_path):
    store=ArtifactStore(tmp_path/'r','r');llm=MeteredLLM(Fake([{},{}]),store,1)
    llm.call('writer')
    with pytest.raises(BudgetExceeded):llm.call('repair')

def test_compile_preserves_beat_partition(tmp_path):
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('source',{})
    lines=[{'id':f'L{i:03}','text':f'第{i}个动作。'} for i in range(1,6)]
    sources=[{'id':l['id'],'beat_id':b,'artifact_id':parent} for l,b in zip(lines,['B1','B1','B2','B3','B4'])]
    f={'name':'test','persona':'温柔','topic':'生活','background':''}
    content,_=compile_content(SimpleNamespace(store=store),f,'\n'.join(l['text'] for l in lines),lines,parent,sources,HarnessFlags())
    assert [len(u['lines']) for u in content['units']]==[2,1,1,1]
    state=to_state(content);assert [l.id for l in state.script_lines]==[l['id'] for l in lines]
    with pytest.raises(GateBlocked):to_state(dict(content,release_ready=False))

def test_no_humor_opportunity_keeps_exact_source(tmp_path):
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('approved',{})
    llm=Fake([{'line_id':None,'candidates':[]}]);nodes=NodeRunner(llm,store,HarnessFlags())
    lines=[{'id':'L001','text':'我把花重新别好。'}]
    text,out,_=humor_pass(nodes,{},lines[0]['text'],lines,parent,HarnessFlags())
    assert text==lines[0]['text'] and out==lines and llm.calls==1

def test_pairwise_position_bias_falls_back(tmp_path):
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('approved',{})
    c={'id':'H1','text':'我把花别在了饭盒上。','expectation':'花','surprise':'饭盒','resolution':'手滑','delivery':'停顿'}
    llm=Fake([{'line_id':'L001','candidates':[c]},{'winner':'A','reason':'a'},{'winner':'A','reason':'a'}])
    flags=HarnessFlags(humor_candidates=1);nodes=NodeRunner(llm,store,flags)
    lines=[{'id':'L001','text':'我把花重新别好。'}]
    text,out,_=humor_pass(nodes,{},lines[0]['text'],lines,parent,flags)
    assert text==lines[0]['text'] and out==lines and llm.calls==3

def test_runtime_does_not_change_events_and_finalizes_on_close(tmp_path):
    class Engine:
        _harness_directory=tmp_path
        _harness_content={'release_ready':True,'text':'你好','lines':[{'id':'L001','text':'你好'}]}
        @trace_runtime
        def run(self):
            yield {'text':'你好','audio':b'abc','line_id':'L001'}
            yield {'text':'结束'}
    gen=Engine().run();event=next(gen);gen.close()
    assert event['audio']==b'abc'
    manifest=json.loads(next((tmp_path/'runtime').glob('*/run.json')).read_text())
    assert manifest['status']=='interrupted' and manifest['events']==1
    events=[json.loads(x) for x in next((tmp_path/'runtime').glob('*/events.jsonl')).read_text().splitlines()]
    assert events[-1]['output']['audio']['bytes']==3

def test_checkpoint_verifies_config_and_hashes(tmp_path):
    from echuu.harness.checkpoint import import_checkpoint
    flags=HarnessFlags();fixture={'id':'x'}
    old=ArtifactStore(tmp_path/'old','old');old.add('run_config',{'fixture':fixture,'flags':flags.to_dict(),'model':'test'})
    old.add('provider_request',{});old.add('node_result',{'value':1},cache_key='key',node_name='test')
    new=ArtifactStore(tmp_path/'new','new')
    cache,calls=import_checkpoint(old.directory,new,fixture,flags,'test')
    assert cache['key'][0]=={'value':1} and calls==1
    with pytest.raises(ValueError,match='config mismatch'):import_checkpoint(old.directory,new,fixture,replace(flags,seed=43),'test')
    path=old.directory/'events.jsonl';path.write_text(path.read_text().replace('"value":1','"value":2'))
    with pytest.raises(ValueError,match='hash mismatch'):import_checkpoint(old.directory,new,fixture,flags,'test')

def test_production_runs_full_graph_with_recorded_selection(tmp_path):
    from echuu.harness.production import run_production
    fixture={'id':'test','name':'林桥','persona':'修理工，干脆','background':'义卖','topic':'标签贴反','evidence':[{'id':'e1','claim':'标签贴反后换回'}]}
    intent={'id':'I1','premise':'标签','desire':'帮忙','obstacle':'贴反','action':'交换','outcome':'修正','evidence_ids':['e1']}
    beats=[{'id':f'B{i}','action':a,'change':a,'purpose':a,'evidence_ids':['e1']} for i,a in enumerate(['看到牌子','顾客询问','发现贴反','换回牌子'],1)]
    values=[{'candidates':[intent]},{'assessments':[{'id':'I1','pass':True,'reason':'有结果'}],'selected_id':'I1'},
            {'candidates':[{'id':'O1','premise':'标签小事','mechanisms':['everyday'],'beats':beats}]},
            {'assessments':[{'id':'O1','pass':True,'reason':'因果连贯'}],'selected_id':'O1'}]
    for i,text in enumerate(['我刚到摊位就看见两张牌子。','客人拿起一整袋，问我能不能试吃。','我低头看了半天，总算发现标签贴反了。','我换回标签，客人买好点心，冲我晃了晃袋子。'],1):
        values.append({'candidates':[{'id':f'U{i}C1','lines':[{'text':text,'origin':'evidence','evidence_ids':['e1']}]}]})
    values.append({'premise':'标签贴反，发现后换回。','gates':{k:{'pass':True,'reason':'完整','line_ids':[]} for k in ('complete','clear','persona','concrete','shareable')}})
    flags=HarnessFlags(intent_candidates=1,outline_candidates=1,local_repair=False)
    path,manifest,content=run_production(fixture,Fake(values),tmp_path,flags)
    assert manifest['status']=='completed' and manifest['llm_call_count']==9
    assert len(content['units'])==4 and len(content['lineage'])==4
    assert (path/'text.txt').read_text()==content['text']

def test_live_steering_restores_rejected_candidate(tmp_path,monkeypatch):
    from echuu.harness.runtime import guard_steering
    store=ArtifactStore(tmp_path/'runtime','runtime')
    line=SimpleNamespace(id='L001',text='原本的咖喱。')
    class Engine:
        _harness_directory=tmp_path
        _harness_runtime_store=store
        _harness_runtime_llm=None
        _harness_content={'fixture':{}}
        state=SimpleNamespace(show=SimpleNamespace(units=[SimpleNamespace(lines=[line])],story_core=None))
        @guard_steering
        def steer(self):line.text='我施法变出咖喱。';return 'changed'
    def rejected(text,fixture,llm,store,parent,rounds):return text,[],{'status':'needs_review'},parent
    monkeypatch.setattr('echuu.harness.repair.run_repair',rejected)
    Engine().steer();assert line.text=='原本的咖喱。'


def test_bad_live_reply_cannot_reach_synthesis(tmp_path):
    from echuu.harness.runtime import approve_reply
    store=ArtifactStore(tmp_path/'runtime','runtime')
    engine=SimpleNamespace(_harness_directory=tmp_path,_harness_runtime_store=store,_harness_runtime_llm=Fake([{'pass':False,'reason':'人设冲突','quote':'我施法'},{'pass':True,'reason':'通顺','quote':'我施法'}]),_harness_content={'fixture':{}})
    assert not approve_reply(engine,'我施法变出咖喱。')

def test_delivery_keeps_untargeted_lines(tmp_path,monkeypatch):
    from echuu.harness.delivery import prepare_delivery
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('source',{})
    nodes=NodeRunner(Fake([{'patches':[{'id':'L001','text':'我扶稳凳子，让她挂好纸花。'}]}]),store,HarnessFlags())
    monkeypatch.setattr('echuu.harness.delivery.run_repair',lambda text,f,llm,s,p,n:(text,[],{'status':'accepted'},p))
    monkeypatch.setattr('echuu.harness.pipeline.assess_story',lambda n,f,t,p:(True,{},p))
    lines=[{'id':'L001','text':'（扶稳凳子）你挂吧。'},{'id':'L002','text':'她笑了。'}]
    text,changed,_=prepare_delivery(nodes,{},'（扶稳凳子）你挂吧。\n她笑了。',lines,parent)
    assert changed[1]==lines[1] and '扶稳凳子' in text and '（' not in text

def test_arbitration_cannot_escalate_style_to_major_review(tmp_path):
    from echuu.harness.repair import arbitrate_issues
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('source',{})
    bad={'decisions':[{'issue_id':'I000','decision':'review','rule_ids':['P_STYLE'],'reason':'比喻是否合适需复核','review_scope':'identity'}]}
    llm=Fake([bad,bad]);issues=[{'id':'L001','code':'style_repetition','decision':'rewrite','rule_ids':['P_STYLE'],'reason':'重复','origin':'story'}]
    with pytest.raises(ValueError,match='major canon scope'):arbitrate_issues([{'id':'L001','text':'纸花像在点头。'}],{},issues,llm,store,parent)

def test_humor_compares_all_candidates_not_first_improvement(tmp_path,monkeypatch):
    store=ArtifactStore(tmp_path/'r','r');parent=store.add('source',{})
    candidates=[{'id':f'H{i}','text':f'我数了{i}次标签。','expectation':'价格','surprise':'数次数','resolution':'认真','delivery':'停一下'} for i in range(1,4)]
    votes=[{'winner':w,'reason':'更自然'} for w in ['B','A']*3]
    nodes=NodeRunner(Fake([{'line_id':'L001','candidates':candidates},*votes]),store,HarnessFlags())
    monkeypatch.setattr('echuu.harness.humor.run_repair',lambda t,f,llm,s,p,n:(t,[],{'status':'accepted'},p))
    monkeypatch.setattr('echuu.harness.pipeline.assess_story',lambda n,f,t,p:(True,{},p))
    text,_,_=humor_pass(nodes,{},'我数了标签。',[{'id':'L001','text':'我数了标签。'}],parent,HarnessFlags())
    assert text=='我数了3次标签。' and nodes.llm.calls==7

def test_runtime_generation_and_judges_share_budget_and_restore_clients(tmp_path):
    original=object();component=SimpleNamespace(llm=original)
    class Engine:
        llm=Fake([{}])
        story_steerer=component
        _harness_directory=tmp_path
        _harness_content={'release_ready':True,'text':'你好','lines':[{'id':'L001','text':'你好'}]}
        @trace_runtime
        def run(self):
            assert self.story_steerer.llm is self._harness_runtime_llm
            self.story_steerer.llm.generate('steering')
            yield {'text':'你好'}
    list(Engine().run());assert component.llm is original
    events=[json.loads(s) for s in next((tmp_path/'runtime').glob('*/events.jsonl')).read_text().splitlines()]
    assert sum(e['node']=='provider_request' for e in events)==1
