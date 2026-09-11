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
        g.generate('角色','人设','背景','话题',character_config={'output_format':'shareable_clip'})
    assert llm.calls==5 and g.last_trace['status']=='needs_review'

def test_contrast_with_dash_does_not_escape_guard():
    from echuu.generators.legacy_v4 import issues_for
    assert issues_for([{'id':'L001','text':'不是修伞，也不是修横幅——是把两代人没说完的话接上了。'}],{})
