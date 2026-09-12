import pytest
from echuu.generators.shareable_clip import validate_review,validate_draft

def draft():
    return {'lines':[{'id':f'L{i+1:03d}','text':'这是一句具体的事件对话。'} for i in range(5)],'quotables':[{'line_id':'L001','quote':'具体的事件'}]}

def test_review_cannot_pass_with_issues_or_invent_evidence():
    d=draft()
    with pytest.raises(ValueError,match='contradictory'):
        validate_review({'verdict':'pass','issues':[{'id':'L001','quote':'具体','reason':'test'}]},d)
    with pytest.raises(ValueError,match='invented'):
        validate_review({'verdict':'revise','issues':[{'id':'L001','quote':'根本不存在','reason':'test'}]},d)

def test_clip_requires_real_quotable_and_length_not_metadata():
    d=draft();budget={'min_chars':200,'max_chars':500}
    assert any(x['code']=='duration' for x in validate_draft(d,budget,{}))
    d['quotables'][0]['quote']='假金句'
    assert any(x['code']=='invented_quotable' for x in validate_draft(d,budget,{}))

def test_clip_retains_previous_style_guards():
    d=draft();d['lines'][0]['text']='不是舍不得换，是每次打开盖子，手心都先热一下。喵！'
    codes={x['code'] for x in validate_draft(d,{'min_chars':1,'max_chars':500},{})}
    assert {'defensive_contrast','unassigned_tic'}<=codes

def test_repair_requires_fresh_review_and_rejection_is_not_playable():
    """Bounded to a single round here (max_repair_rounds=1) so a persistently
    rejecting reviewer still exhausts and fails cleanly — quality gates are
    never relaxed just because the round budget ran out."""
    import json
    from echuu.generators.legacy_v4 import ScriptGeneratorV4
    plan={'candidates':[dict(id='a',premise='误会',expectation='拒绝',surprise='偷偷帮忙',resolution='一起完成',character_choice='指出嘴硬',share_reason='亲近')],'selected_id':'a','voice_design':{'provenance':'改编'}}
    d=draft()
    for l in d['lines']:l['text']='昨天我问他为什么站在门口，他说只是顺路。我就指着他手里那把伞问，天气这么好，怎么还带了两把伞。他看了看窗外，我才知道他一直在等人。'
    d['quotables']=[{'line_id':'L001','quote':'怎么还带了两把伞'}]
    rejection={'verdict':'revise','issues':[{'id':'L001','quote':'昨天','reason':'故事重复'}]}
    class Fake:
        def __init__(self):self.values=iter([plan,d,rejection,d,rejection]);self.calls=0
        def call(self,*args,**kwargs):self.calls+=1;return json.dumps(next(self.values),ensure_ascii=False)
    llm=Fake();g=ScriptGeneratorV4(llm)
    with pytest.raises(ValueError,match='semantic repair unresolved'):
        g.generate('角色','人设','背景','话题',character_config={'output_format':'shareable_clip','max_repair_rounds':1})
    assert llm.calls==5 and g.last_trace['status']=='needs_review'
    assert g.last_trace['rounds_attempted']==1
    assert len(g.last_trace['repair_rounds'])==1


def test_bounded_repair_succeeds_on_a_later_round_after_an_earlier_one_fails():
    """A round that doesn't fully resolve the reviewer's issues doesn't fail
    the whole generation immediately — it gets another bounded, targeted
    attempt, and a later round that actually fixes the flagged line passes.
    Only the flagged line (L001) changes; the rest stay verbatim throughout."""
    import json
    from echuu.generators.legacy_v4 import ScriptGeneratorV4
    plan={'candidates':[dict(id='a',premise='误会',expectation='拒绝',surprise='偷偷帮忙',resolution='一起完成',character_choice='指出嘴硬',share_reason='亲近')],'selected_id':'a','voice_design':{'provenance':'改编'}}
    d=draft()
    for l in d['lines']:l['text']='昨天我问他为什么站在门口，他说只是顺路。我就指着他手里那把伞问，天气这么好，怎么还带了两把伞。他看了看窗外，我才知道他一直在等人。'
    d['quotables']=[{'line_id':'L001','quote':'怎么还带了两把伞'}]
    rejection={'verdict':'revise','issues':[{'id':'L001','quote':'昨天','reason':'故事重复'}]}
    l1_fixed='昨天我问他为什么站在门口，他说只是顺路，我没信，追问了一句他才承认，其实一直在等人，只是不好意思直接说。'
    fixed={'lines':[{'id':'L001','text':l1_fixed}]+[dict(l) for l in d['lines'][1:]],
           'quotables':[{'line_id':'L001','quote':'一直在等人'}]}
    passing={'verdict':'pass','issues':[],'story_summary':'ok','share_reason':'ok'}
    class Fake:
        def __init__(self):
            # plan, draft, initial-review(reject), round1 repair(still bad), round1 review(reject),
            # round2 repair(fixed), round2 review(pass)
            self.values=iter([plan,d,rejection,d,rejection,fixed,passing]);self.calls=0
        def call(self,*args,**kwargs):self.calls+=1;return json.dumps(next(self.values),ensure_ascii=False)
    llm=Fake();g=ScriptGeneratorV4(llm)
    lines=g.generate('角色','人设','背景','话题',character_config={'output_format':'shareable_clip','max_repair_rounds':3})
    assert g.last_trace['status']=='completed'
    assert g.last_trace['rounds_attempted']==2
    assert len(g.last_trace['repair_rounds'])==2
    by_id={l.id:l.text for l in lines}
    assert by_id['L001']==l1_fixed
    assert all(by_id[f'L{i+1:03d}']==d['lines'][i]['text'] for i in range(1,5))


def test_scoped_repair_round_never_changes_untargeted_lines():
    """Programmatic backstop: even if the model's repair response edits a
    line outside this round's flagged targets, that edit is discarded and
    the original text for that line is kept — repair must stay targeted."""
    import json
    from echuu.generators.legacy_v4 import ScriptGeneratorV4
    plan={'candidates':[dict(id='a',premise='误会',expectation='拒绝',surprise='偷偷帮忙',resolution='一起完成',character_choice='指出嘴硬',share_reason='亲近')],'selected_id':'a','voice_design':{'provenance':'改编'}}
    filler='这是一句正常没问题的台词，把事情经过、原因、中间的波折和最后的结果都交代得很清楚明白，听众完全能听懂到底发生了什么事。'
    l1_before='不是我不想早点去解决这件事，是那天临时有别的安排走不开，后来专门找了个合适的时间登门当面解释清楚，对方听完也没有再计较。'
    l1_after='后来才知道那天真的是临时有别的安排走不开，专门登门当面解释清楚了这场误会，对方听完也没有再计较。'
    d=draft()
    for i,l in enumerate(d['lines']):
        l['text']=l1_before if i==0 else filler
    d['quotables']=[{'line_id':'L002','quote':'把事情经过、原因、中间的波折'}]
    over_reaching_repair=draft()
    for i,l in enumerate(over_reaching_repair['lines']):
        if i==0:l['text']=l1_after
        elif i==1:l['text']='这一行被模型越界改写了，不应该出现在最终结果里，也不该被采纳。'
        else:l['text']=d['lines'][i]['text']
    over_reaching_repair['quotables']=[{'line_id':'L001','quote':'专门登门当面解释清楚了这场误会'}]
    passing={'verdict':'pass','issues':[],'story_summary':'ok','share_reason':'ok'}
    class Fake:
        def __init__(self):
            self.values=iter([plan,d,passing,passing]);self.calls=0
        def call(self,prompt,system='',**kwargs):
            self.calls+=1
            if '修复编辑' in system:return json.dumps(over_reaching_repair,ensure_ascii=False)
            return json.dumps(next(self.values),ensure_ascii=False)
    llm=Fake();g=ScriptGeneratorV4(llm)
    # L001 fails the defensive-contrast guard (不是...是...), forcing a scoped
    # round targeting only L001; the fake reviewer always passes so the loop
    # stops after that single scoped round.
    lines=g.generate('角色','人设','背景','话题',character_config={'output_format':'shareable_clip'})
    by_id={l.id:l.text for l in lines}
    assert by_id['L002']==d['lines'][1]['text']
    assert '越界改写' not in by_id['L002']
    round_record=g.last_trace['repair_rounds'][0]
    assert round_record['targets']==['L001']
    assert g.last_trace['status']=='completed'


def test_final_failure_carries_structured_diagnostic_not_just_a_string():
    """On exhaustion, the raised error and the trace both carry the specific
    flagged lines and every round's before/after content — not just the
    bare 'constraints unresolved' string."""
    import json
    from echuu.generators.legacy_v4 import ScriptGeneratorV4
    from echuu.generators.shareable_clip import ClipGenerationError
    plan={'candidates':[dict(id='a',premise='误会',expectation='拒绝',surprise='偷偷帮忙',resolution='一起完成',character_choice='指出嘴硬',share_reason='亲近')],'selected_id':'a','voice_design':{'provenance':'改编'}}
    too_short=draft()  # 5 short lines: fails the duration floor and never gets fixed
    passing={'verdict':'pass','issues':[],'story_summary':'s','share_reason':'r'}
    class Fake:
        def __init__(self):
            self.values=iter([plan,too_short,passing,passing,passing]);self.calls=0
        def call(self,prompt,system='',**kwargs):
            self.calls+=1
            if '修复编辑' in system:return json.dumps(too_short,ensure_ascii=False)
            return json.dumps(next(self.values),ensure_ascii=False)
    llm=Fake();g=ScriptGeneratorV4(llm)
    with pytest.raises(ClipGenerationError) as excinfo:
        g.generate('角色','人设','背景','话题',character_config={'output_format':'shareable_clip','max_repair_rounds':2})
    diagnostic=excinfo.value.diagnostic
    assert diagnostic['reason']=='clip constraints unresolved'
    assert diagnostic['rounds_attempted']==2
    assert any(p['code']=='duration' for p in diagnostic['final_structural_problems'])
    assert len(diagnostic['repair_rounds'])==2
    for round_record in diagnostic['repair_rounds']:
        assert 'before_lines' in round_record and 'after_lines' in round_record
    assert g.last_trace['repair_rounds']==diagnostic['repair_rounds']

def test_contrast_with_dash_does_not_escape_guard():
    from echuu.generators.legacy_v4 import issues_for
    assert issues_for([{'id':'L001','text':'不是修伞，也不是修横幅——是把两代人没说完的话接上了。'}],{})
