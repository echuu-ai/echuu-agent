import json
import random
import pytest
from echuu.generators.legacy_v4 import ScriptGeneratorV4,duration_budget
from echuu.core.structure_breaker import StructureBreaker


def story(first=None):
    texts=['今天装便当的时候，我试了一下新饭盒。饭刚放进去，盖子就合不上了，只好先把勺子放下。',
           '桃矢还在旁边等，我就问他要不要用这个。按他平时的饭量装，盖子倒是一下就盖好了。',
           '我拿回原来的大饭盒，把自己那份装进去。这回不用挤，也没有把咖喱弄得四处都是。',
           '两份午饭都装好了。桃矢拎走新盒子，我还是带旧的，今天总算知道自己需要多大的了。']
    if first:texts[0]=first
    return {'speech_profile':dict(register='温和坦率',sentence_shape='短句',attention='照顾对方',humor='轻轻承认食量',basis='性格推导'),
            'story':dict(setup='装饭',problem='盖不上',action='换大盒',result='装好两份'),
            'lines':[{'id':f'L{i+1:03d}','text':text} for i,text in enumerate(texts)]}

class LLM:
    def __init__(self,values):self.values=iter(values);self.prompts=[]
    def call(self,prompt,**kw):self.prompts.append((prompt,kw));return json.dumps(next(self.values),ensure_ascii=False)

def test_default_breaker_never_appends_generic_tics():
    for seed in range(25):
        random.seed(seed);lines=[{'text':'饭盒盖好了。'},{'text':'桃矢拿走了新盒子。'}]
        assert StructureBreaker().break_structure(lines,'午饭')==lines

def test_spoken_writer_preserves_results_and_explicit_speech_tics():
    llm=LLM([story()]);g=ScriptGeneratorV4(llm)
    lines=g.generate('雪兔','温和','背景','话题',character_config={'target_seconds':48,'semantic_review':False,'speech_tics':['嗯']})
    assert len(llm.prompts)==1 and len(lines)==4
    assert '两份午饭都装好了' in lines[-1].text
    assert '"speech_tics": ["嗯"]' in llm.prompts[0][0]
    assert g.last_trace['story']['result']=='装好两份'

def test_one_bad_contrast_gets_local_repair_without_touching_other_lines():
    original=story('不是舍不得换，是每次打开盖子，手心都先热一下。我试了一下新饭盒，结果盖子合不上。')
    fixed=story()['lines'][0]['text'];llm=LLM([original,{'patches':[{'id':'L001','text':fixed}]}]);g=ScriptGeneratorV4(llm)
    lines=g.generate('雪兔','温和','背景','话题',character_config={'target_seconds':48,'semantic_review':False})
    assert len(llm.prompts)==2 and [l.text for l in lines[1:]]==[l['text'] for l in original['lines'][1:]]
    assert '不是' not in lines[0].text

def test_second_invalid_style_is_blocked_not_sent_to_playback():
    bad=story('不是舍不得换，是每次打开盖子，手心都先热一下。我试了一下新饭盒，结果盖子合不上。')
    llm=LLM([bad,{'patches':[{'id':'L001','text':bad['lines'][0]['text']}]}])
    with pytest.raises(ValueError,match='unresolved'):ScriptGeneratorV4(llm).generate('雪兔','温和','背景','话题',character_config={'target_seconds':48,'semantic_review':False})
    assert len(llm.prompts)==2

def test_budget_scales_with_requested_duration_and_rejects_invalid():
    assert duration_budget({'target_seconds':60})['max_chars']<duration_budget({'target_seconds':90})['min_chars']
    with pytest.raises(ValueError):duration_budget({'target_seconds':1})

def test_latency_shortens_next_story_without_overriding_explicit_duration():
    from echuu.generators.legacy_v4 import adapt_duration_config
    old={'generation_seconds':50,'budget':{'target_seconds':75}}
    assert adapt_duration_config({},old)['target_seconds']==56.25
    assert adapt_duration_config({'target_seconds':90},old)['target_seconds']==90
    assert adapt_duration_config({},dict(generation_seconds=20,budget={'target_seconds':56.25}))['target_seconds']==56.25

def test_review_patches_only_cited_line_and_rejects_invented_quote():
    from echuu.generators.legacy_v4 import review_spoken
    obj=story();review={'summary':'新饭盒太小，换大盒装好。','issues':[{'id':'L001','quote':'今天装便当的时候','code':'story_repetition','reason':'第一行保留开场就够了'}],'patches':[{'id':'L001','text':'今天试了新饭盒，才知道它有多小。'}]}
    out,_=review_spoken(obj,{},lambda p:json.dumps(review))
    assert out['lines'][1:]==obj['lines'][1:]
    review['issues'][0]['quote']='原稿不存在的句子'
    with pytest.raises(ValueError,match='invented'):review_spoken(obj,{},lambda p:json.dumps(review))


def test_default_writer_never_reads_raw_example_sampler():
    class ForbiddenSampler:
        def __getattr__(self, name):
            raise AssertionError('raw few-shot access: ' + name)
    llm = LLM([story()])
    generator = ScriptGeneratorV4(llm, ForbiddenSampler())
    generator.generate('角色', '人设', '背景', '话题', character_config={'target_seconds':48, 'semantic_review':False})
    assert generator.last_trace['status'] == 'completed'
