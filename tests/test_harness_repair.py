import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from echuu.harness.store import ArtifactStore
from echuu.harness.repair import run_repair, line_items, validate_review, apply_patches, deterministic_issues

FIXTURE={'id':'test','name':'角色','persona':'温柔的普通人','background':'不是直播，和朋友做午饭。','evidence':[{'id':'e1','claim':'午饭装好'}], 'topic':'午饭'}
TEXT='我以前装过一次太满的饭盒，后来学会给盒盖留点空间。\n今天我们把午饭装好，一起带出门。'


def keep_review(lines,addition='none'):
    return {'lines':[{'id':l['id'],'decision':'keep','code':'ok','rule_ids':[],'reason':'符合场景','addition':addition} for l in lines if l['text']],
            'story':{'complete':True,'reason':'午饭装好','repair_line_ids':[]}}


class FakeLLM:
    def __init__(self,review=None,patch=None,arbitration=None):
        self.review=review or (lambda lines:keep_review(lines))
        self.patch=patch
        self.arbitration=arbitration
        self.calls=[]
    def call(self,prompt,max_tokens=0):
        self.calls.append(prompt)
        data=json.loads(prompt.split('DATA:\n',1)[1].split('\n上次输出结构无效：',1)[0])
        if 'decisions 数组' in prompt:
            result=self.arbitration(data) if self.arbitration else {'decisions':[{'issue_id':i['issue_id'],'decision':'uphold','rule_ids':i['rule_ids'],'reason':'confirmed'} for i in data['proposed']]}
            return json.dumps(result,ensure_ascii=False)
        if 'patches 数组' in prompt:
            return json.dumps(self.patch(data),ensure_ascii=False)
        return json.dumps(self.review(data['lines']),ensure_ascii=False)


def run(tmp_path,llm,text=TEXT,rounds=2):
    store=ArtifactStore(tmp_path/'run','run')
    parent=store.add('draft',{'text':text})
    return run_repair(text,FIXTURE,llm,store,parent,rounds),store


def test_compatible_past_experience_is_preserved(tmp_path):
    llm=FakeLLM(review=lambda lines:keep_review(lines,'compatible'))
    (text,_,result,_),_=run(tmp_path,llm)
    assert text==TEXT and result['status']=='accepted' and result['rounds']==[]
    assert len(llm.calls)==2
    assert all(a['scope']=='run_only' for a in result['compatible_additions'])


def test_false_pass_cannot_override_nonlive_rule(tmp_path):
    llm=FakeLLM(patch=lambda data:{'patches':[{'id':'L002','replacement':'今天我们把午饭装好，一起带出门。'}]})
    (text,_,result,_),_=run(tmp_path,llm,TEXT+'等会下播看看SC。')
    assert result['status']=='accepted' and '下播' not in text
    assert text.splitlines()[0]==TEXT.splitlines()[0]
    assert len(result['rounds'])==1


def test_missing_line_coverage_fails_closed(tmp_path):
    llm=FakeLLM(review=lambda lines:keep_review(lines[:1]))
    (text,_,result,_),_=run(tmp_path,llm)
    assert result['status']=='needs_review' and text==TEXT
    assert result['valid_critics']==0 and len(llm.calls)==4


def test_conflict_cannot_hide_behind_keep():
    lines=line_items(TEXT);review=keep_review(lines)
    review['lines'][0].update(code='character_conflict',rule_ids=['persona'])
    with pytest.raises(ValueError,match='cannot be passed'):
        validate_review(json.dumps(review),lines,FIXTURE)


def test_unsupported_is_not_a_repair_category():
    lines=line_items(TEXT);review=keep_review(lines)
    review['lines'][0].update(decision='rewrite',code='unsupported_detail',rule_ids=['P_CREATIVE'])
    with pytest.raises(ValueError):
        validate_review(json.dumps(review),lines,FIXTURE)


def test_patcher_cannot_touch_good_line_or_erase_story():
    lines=line_items(TEXT)
    with pytest.raises(ValueError,match='untargeted'):
        apply_patches(json.dumps({'patches':[{'id':'L001','replacement':'重写'}]}),lines,['L002'],20)
    with pytest.raises(ValueError,match='erased'):
        apply_patches(json.dumps({'patches':[{'id':'L001','replacement':''},{'id':'L002','replacement':''}]}),lines,['L001','L002'],20)


def test_rejected_patch_preserves_previous_draft_and_is_bounded(tmp_path):
    llm=FakeLLM(patch=lambda data:{'patches':[{'id':'L001','replacement':'不要改好段落'}]})
    original=TEXT+'等会下播看看SC。'
    (text,_,result,_),_=run(tmp_path,llm,original)
    assert text==original and result['status']=='needs_review'
    assert len(result['rounds'])==2 and all(r['decision']=='rejected' for r in result['rounds'])


def test_nonlive_rule_does_not_ban_actual_live_context():
    assert not deterministic_issues(line_items('看看SC和弹幕。'),{'background':'正在直播，面向观众。'})


def test_unapproved_replay_never_calls_tts(tmp_path,monkeypatch):
    import echuu.harness.runner as runner
    source=tmp_path/'source';source.mkdir()
    (source/'run.json').write_text(json.dumps({'run_id':'source','character':FIXTURE,'variant':'legacy_v4'}))
    (source/'text.txt').write_text(TEXT)
    def blocked(text,fixture,llm,store,parent,max_rounds):
        result={'status':'needs_review','rounds':[],'final_issues':[]}
        return text,line_items(text),result,store.add('repair_result',result,parents=[parent])
    monkeypatch.setattr(runner,'run_repair',blocked)
    def unexpected(*args,**kwargs):
        pytest.fail('TTS called for an unapproved repair')
    class Engine:
        def __init__(self):
            self.llm=SimpleNamespace()
            self.llm_gen=SimpleNamespace(model='fake',call=unexpected,generate=unexpected)
            self.debug_trace={'stages':[]}
            self.tts=SimpleNamespace(enabled=True,synthesize=unexpected)
    path,result=runner.run_case(FIXTURE,'legacy_v4',tmp_path/'output',engine_factory=Engine,source_run=source)
    assert result['status']=='needs_review' and result['audio_skipped_reason']=='repair_not_approved'
    assert not (path/'audio.wav').exists()


def test_overzealous_abstract_summary_judgment_is_dismissed(tmp_path):
    def criticize(lines):
        result=keep_review(lines,'compatible')
        result['lines'][0].update(decision='rewrite',code='abstract_summary',rule_ids=['P_CONCRETE'],reason='从经历总结了生活习惯')
        return result
    def dismiss(data):
        return {'decisions':[{'issue_id':i['issue_id'],'decision':'dismiss','rule_ids':['P_CREATIVE'],'reason':'具体经验不是空泛升华'} for i in data['proposed']]}
    llm=FakeLLM(review=criticize,arbitration=dismiss)
    (text,_,result,_),_=run(tmp_path,llm)
    assert text==TEXT and result['status']=='accepted' and result['rounds']==[]
    assert len(llm.calls)==3


def test_invalid_arbitration_cannot_release_or_modify(tmp_path):
    def criticize(lines):
        result=keep_review(lines)
        result['lines'][0].update(decision='rewrite',code='abstract_summary',rule_ids=['P_CONCRETE'])
        return result
    (text,_,result,_),_=run(tmp_path,FakeLLM(review=criticize,arbitration=lambda data:{'decisions':[]}))
    assert text==TEXT and result['status']=='needs_review' and not result['arbitration_valid']


def test_semantic_duplicate_cannot_make_deterministic_issue_dismissible(tmp_path):
    def criticize(lines):
        result=keep_review(lines)
        result['lines'][-1].update(decision='rewrite',code='meta_leak',rule_ids=['P_SCENE'])
        return result
    def dismiss(data):
        return {'decisions':[{'issue_id':i['issue_id'],'decision':'dismiss','rule_ids':['P_CREATIVE'],'reason':'mistaken pass'} for i in data['proposed']]}
    (text,_,result,_),_=run(tmp_path,FakeLLM(review=criticize,arbitration=dismiss),TEXT+'等会下播看看SC。',rounds=0)
    assert result['status']=='needs_review'
    assert any(i.get('origin')=='deterministic' for i in result['final_issues'])


def test_new_character_conflict_in_patch_rolls_back(tmp_path):
    def criticize(lines):
        result=keep_review(lines)
        if '魔法' in lines[-1]['text']:
            result['lines'][-1].update(decision='rewrite',code='character_conflict',rule_ids=['persona'],reason='普通人不能施法')
        return result
    llm=FakeLLM(review=criticize,patch=lambda data:{'patches':[{'id':'L002','replacement':'今天我用自己的魔法把午饭装好，一起带出门。'}]})
    original=TEXT+'等会下播看看SC。'
    (text,_,result,_),_=run(tmp_path,llm,original)
    assert text==original and result['status']=='needs_review'
    assert len(result['rounds'])==2 and all(r['decision']=='rejected' for r in result['rounds'])


def test_retry_receives_rejected_candidate_feedback(tmp_path):
    seen=[]
    def criticize(lines):
        result=keep_review(lines)
        if '魔法' in lines[-1]['text']:
            result['lines'][-1].update(decision='rewrite',code='character_conflict',rule_ids=['persona'])
        return result
    def patch(data):
        seen.append(data['rejected_candidates'])
        return {'patches':[{'id':'L002','replacement':'今天我用自己的魔法把午饭装好，一起带出门。' if not data['rejected_candidates'] else TEXT.splitlines()[1]}]}
    (text,_,result,_),_=run(tmp_path,FakeLLM(review=criticize,patch=patch),TEXT+'等会下播看看SC。')
    assert result['status']=='accepted' and text==TEXT
    assert seen[0]==[] and seen[1][0]['issues'][0]['code']=='character_conflict'


def test_uncertain_major_line_does_not_block_other_local_fixes(tmp_path):
    def criticize(lines):
        result=keep_review(lines)
        result['lines'][0].update(decision='review',code='major_invention',addition='major',rule_ids=['P_MAJOR'],reason='需确认重大背景')
        return result
    llm=FakeLLM(review=criticize,patch=lambda data:{'patches':[{'id':'L002','replacement':TEXT.splitlines()[1]}]})
    (text,_,result,_),_=run(tmp_path,llm,TEXT+'等会下播看看SC。')
    assert text==TEXT and result['status']=='needs_review'
    assert len(result['rounds'])==1 and result['rounds'][0]['decision']=='working_candidate'
    assert result['final_issues'][0]['decision']=='review'


def test_long_copied_dialogue_is_caught_when_judges_pass(tmp_path):
    segment='我把新盒推给你，说你先挑。你拿起来晃了晃，说挺轻啊，我就笑了。那是我留给你的一份，还没有动过勺，盖子也擦得干干净净。'
    original=segment+'\n所以这一次，'+segment
    (text,_,result,_),_=run(tmp_path,FakeLLM(),original,rounds=0)
    assert result['status']=='needs_review'
    assert result['final_issues'][0]['origin']=='deterministic'
    assert not deterministic_issues(line_items('好啦。\n好啦。'),FIXTURE)


def test_observed_thirty_seven_character_dialogue_copy():
    span='你拿起来晃了晃，说‘挺轻啊’，我就笑了。轻？那是我留给你的一份，没动过勺。'
    found=deterministic_issues(line_items('后来，'+span+'\n所以，'+span),FIXTURE)
    assert any(i['id']=='L002' and i['origin']=='deterministic' for i in found)
