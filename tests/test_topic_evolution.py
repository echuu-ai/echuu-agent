from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from echuu.live.topic_evolution import TopicEvolution
from echuu.live.state import Danmaku
from echuu.core.story_core import StoryCore
from tests.test_danmaku_interleave import _engine_with, _make_show, FakeLLM


def proposal(e, action='transition', topic='怎么关心家人'):
    return dict(action=action, topic=e.state.topic if action != 'transition' else topic,
                direction='' if action == 'reply' else '从个人消费预算聊到为家人留出时间和心意',
                reason='观众的问题和刚才讲的消费取舍有关',
                lines=[] if action == 'reply' else [
                    {'id': l.id, 'text': f'家人的生日安排第{i}项，要认真想想喜欢的口味。'}
                    for i, (_, _, l) in enumerate(e._upcoming_script_lines())])


def attach(e, value):
    llm = FakeLLM(json.dumps(value, ensure_ascii=False))
    e.topic_evolution = TopicEvolution(llm)
    return llm


def texts(e):
    return [line.text for unit in e.state.show.units for line in unit.lines]


def test_greeting_is_reply_only():
    e = _engine_with(_make_show(), [])
    old = texts(e)
    attach(e, proposal(e, 'reply'))
    dm = Danmaku.from_text('晚上好')
    assert '仅回应' in e._steer_remaining_show(dm)
    assert texts(e) == old and e.state.topic == '商稿稿费'
    assert dm.action == 'reply' and not dm.story_changed and dm.outcome == 'accepted'


def test_expand_changes_the_end_but_keeps_topic_and_spoken_line():
    e = _engine_with(_make_show(), [])
    old = texts(e)
    e.state.show.units[-1].lines[-1].key_info = ['旧稿关键词']
    attach(e, proposal(e, 'expand'))
    dm = Danmaku.from_text('稿费拿到手之前怎么安排生活费？')
    e._steer_remaining_show(dm)
    assert texts(e)[0] == old[0]
    assert all(a != b for a, b in zip(texts(e)[1:], old[1:]))
    assert not e.state.show.units[-1].lines[-1].key_info
    assert e.state.topic == '商稿稿费' and dm.action == 'expand'
    assert dm.story_changed and dm.current_topic == '商稿稿费'


def test_next_interaction_remembers_new_topic_persona_and_spoken_content():
    e = _engine_with(_make_show(), [])
    e.state.persona, e.state.background = '节俭又关心家人的插画师', '普通上班族'
    e.state.spoken_history = [{'id': 'past', 'text': '妈妈生日快到了。', 'kind': 'story'}]
    e.state.show.story_core = StoryCore(spine='旧主轴', twist='旧稿反转')
    attach(e, proposal(e))
    e._steer_remaining_show(Danmaku.from_text('给妈妈买蛋糕'))
    assert e.state.show.story_core.spine != '旧主轴'
    assert e.state.show.story_core.twist == ''
    old_after = texts(e)
    next_llm = attach(e, proposal(e, 'reply'))
    e._steer_remaining_show(Danmaku.from_text('这个蛋糕有什么口味可选？'))
    data = json.loads(next_llm.calls[0].split('DATA：\n', 1)[1])
    assert data['current_topic'] == '怎么关心家人'
    assert data['initial_topic'] == '商稿稿费'
    assert data['persona'] == e.state.persona and data['background'] == e.state.background
    assert data['spoken_history'][0]['text'] == '妈妈生日快到了。'
    assert data['topic_history'][-1]['from_topic'] == '商稿稿费'
    assert texts(e) == old_after


@pytest.mark.parametrize('text', ['Sent Pebble', '投喂了石头', 'Sent white-pebble', '送给你', ''])
def test_old_rice_ball_clients_are_normalized_for_both_model_calls(text):
    dm = Danmaku.from_input(text, kind='gift', gift_id='white-pebble', amount=8)
    assert dm.gift_name == '饭团' and dm.gift_category == 'food'
    assert '饭团' in dm.text and 'Pebble' not in dm.text and '石头' not in dm.text
    e = _engine_with(_make_show(), [dm])
    llm = attach(e, proposal(e, 'reply'))
    event = e._maybe_interleave_danmaku(0, '刚聊到午饭')
    assert event['danmaku']['text'] == dm.text
    data = json.loads(llm.calls[0].split('DATA：\n', 1)[1])
    assert data['interaction']['gift_name'] == '饭团'
    assert '饭团' in e.danmaku_interleaver.llm.calls[0]
    assert dm.to_public()['gift_name'] == '饭团'


@pytest.mark.parametrize('action', ['reply', 'expand', 'transition'])
def test_same_gift_can_take_any_contextual_action(action):
    e = _engine_with(_make_show(), [])
    attach(e, proposal(e, action))
    dm = Danmaku.from_input('谢谢你记得我没吃饭', kind='gift', gift_id='white-pebble', amount=8)
    e._steer_remaining_show(dm)
    assert dm.action == action and dm.outcome == 'accepted'
    assert dm.story_changed == (action != 'reply')


@pytest.mark.parametrize('bad', ['partial', 'reordered', 'duplicate', 'empty', 'oversize', 'bad_action', 'reply_with_patch', 'expand_changes_topic'])
def test_invalid_plan_changes_neither_topic_nor_script(bad):
    e = _engine_with(_make_show(), [])
    before = texts(e)
    value = proposal(e)
    if bad == 'partial': value['lines'].pop()
    if bad == 'reordered': value['lines'].reverse()
    if bad == 'duplicate': value['lines'][1]['id'] = value['lines'][0]['id']
    if bad == 'empty': value['lines'][0]['text'] = ''
    if bad == 'oversize': value['lines'][0]['text'] = '长' * 181
    if bad == 'bad_action': value['action'] = 'invent'
    if bad == 'reply_with_patch': value['action'] = 'reply'
    if bad == 'expand_changes_topic': value['action'] = 'expand'
    attach(e, value)
    dm = Danmaku.from_text('换个话题？')
    e._steer_remaining_show(dm)
    assert texts(e) == before and e.state.topic == '商稿稿费'
    assert not dm.story_changed and dm.outcome == 'fallback'
    assert not getattr(e.state, 'topic_history', [])


def test_provider_error_is_a_reply_without_topic_commit():
    e = _engine_with(_make_show(), [])
    class Down:
        def generate(self, _): raise RuntimeError('network')
    e.topic_evolution = TopicEvolution(Down())
    before = texts(e)
    dm = Danmaku.from_text('生日')
    e._steer_remaining_show(dm)
    assert texts(e) == before and not dm.story_changed


def test_last_line_does_not_promise_future_rewrite():
    e = _engine_with(_make_show(), [])
    e.state.current_unit_idx, e.state.current_line_in_unit = 3, 2
    dm = Danmaku.from_text('聊妈妈')
    assert '无后续' in e._steer_remaining_show(dm)
    assert not dm.story_changed


@pytest.mark.parametrize('accepted', [True, False])
def test_harness_commits_or_restores_topic_script_and_cues_together(tmp_path, monkeypatch, accepted):
    from echuu.harness.store import ArtifactStore
    e = _engine_with(_make_show(), [])
    e.state.show.story_core = StoryCore(spine='旧主轴')
    e.state.show.units[-1].lines[-1].key_info = ['原始线索']
    e.state.topic_history = []
    e._harness_directory = tmp_path
    e._harness_runtime_store = ArtifactStore(tmp_path / 'runtime', 'test')
    e._harness_runtime_llm = None
    e._harness_content = {'fixture': {'topic': e.state.topic}}
    before = texts(e)
    seen = []
    def review(text, fixture, llm, store, aid, attempts):
        seen.append(deepcopy(fixture))
        return None, None, {'status': 'accepted' if accepted else 'rejected'}, aid
    monkeypatch.setattr('echuu.harness.repair.run_repair', review)
    attach(e, proposal(e))
    dm = Danmaku.from_text('聊聊妈妈')
    e._steer_remaining_show(dm)
    assert seen[0]['topic'] == '怎么关心家人'
    assert dm.story_changed is accepted
    if accepted:
        assert texts(e) != before and e.state.topic == '怎么关心家人'
        assert e.state.topic_history and not e.state.show.units[-1].lines[-1].key_info
    else:
        assert texts(e) == before and e.state.topic == '商稿稿费'
        assert e.state.show.topic == '商稿稿费' and not e.state.topic_history
        assert e.state.show.story_core.spine == '旧主轴'
        assert e.state.show.units[-1].lines[-1].key_info == ['原始线索']
        assert dm.outcome == 'rejected'


def test_rendering_uses_new_text_for_audio_and_records_actual_spoken_context():
    e = _engine_with(_make_show(), [Danmaku.from_text('给妈妈买蛋糕')])
    attach(e, proposal(e))
    synthesized = []
    e.tts.synthesize = lambda text, **kw: synthesized.append(text) or b'audio'
    events = list(e.run(max_steps=99, play_audio=False, save_audio=False))
    story = [ev for ev in events if not ev.get('is_danmaku_response')]
    assert '家人的生日' in story[-1]['speech']
    assert story[-1]['speech'] in synthesized
    assert story[-1]['show']['current_topic'] == '怎么关心家人'
    assert e.state.spoken_history[-1]['text'] == story[-1]['speech']


def test_project_root_is_independent_of_checkout_name_and_working_directory(tmp_path, monkeypatch):
    from echuu.live.engine import _find_project_root
    from pathlib import Path
    monkeypatch.chdir(tmp_path)
    assert _find_project_root() == Path(__file__).resolve().parents[1]


def test_continuity_rejection_rolls_back_without_partial_topic_change():
    e = _engine_with(_make_show(), [])
    value = proposal(e)
    class Rejecting(FakeLLM):
        def generate(self, prompt):
            self.calls.append(prompt)
            if prompt.startswith('检查直播话题续写的连续性'):
                return json.dumps({'approved': False, 'reason': '编造了给妈妈买鞋的经历'}, ensure_ascii=False)
            return json.dumps(value, ensure_ascii=False)
    llm = Rejecting()
    e.topic_evolution = TopicEvolution(llm)
    before = texts(e)
    dm = Danmaku.from_text('妈妈生日')
    e._steer_remaining_show(dm)
    assert texts(e) == before and e.state.topic == '商稿稿费' and not dm.story_changed
    assert len(llm.calls) == 4  # One bounded correction, then keep original.
    assert 'correction_required' in llm.calls[2]


def test_unspoken_draft_is_not_presented_as_remembered_facts():
    e = _engine_with(_make_show(), [])
    e.state.show.units[-1].lines[-1].text = '我上次给妈妈买鞋的未播出虚构剧情。'
    llm = attach(e, proposal(e))
    e._steer_remaining_show(Danmaku.from_text('家人'))
    data = json.loads(llm.calls[0].split('DATA：\n', 1)[1])
    assert all(set(line) == {'id'} for line in data['remaining_lines'])
    assert '未播出虚构剧情' not in llm.calls[0]


def test_unfounded_short_reply_falls_back_without_reverting_adopted_topic():
    e = _engine_with(_make_show(), [Danmaku.from_text('聊聊给妈妈准备生日')], reply='我妈最喜欢我送的鞋。')
    attach(e, proposal(e))
    e.topic_evolution.approve_reply = lambda *args, **kwargs: False
    ev = e._maybe_interleave_danmaku(0, '我在想消费预算')
    assert ev['speech'] == '看到你的消息了，我接着聊。'
    assert e.state.topic == '怎么关心家人'
    assert e.state.spoken_history[-1]['text'] == ev['speech']


@pytest.mark.parametrize('greeting', ['晚上好，我继续听。', '你好呀！', '嗯嗯', 'Hello!'])
def test_failed_proposal_cannot_resurrect_on_social_message(greeting):
    e = _engine_with(_make_show(), [])
    e.state.interaction_history = [{'text': '给妈妈买蛋糕聊关心父母', 'disposition': 'not_adopted', 'pending': False}]
    llm = attach(e, proposal(e))  # Would transition if the model were consulted.
    old = texts(e)
    dm = Danmaku.from_text(greeting)
    e._steer_remaining_show(dm)
    assert dm.action == 'reply' and dm.outcome == 'accepted'
    assert texts(e) == old and not llm.calls
    assert dm.decision_trace[0]['code'] == 'social_reply'


def test_greeting_with_substantive_request_still_reaches_model():
    e = _engine_with(_make_show(), [])
    llm = attach(e, proposal(e))
    dm = Danmaku.from_text('晚上好，接着刚才那个给妈妈买蛋糕的话题聊吧')
    e._steer_remaining_show(dm)
    assert llm.calls and dm.story_changed


def test_rejection_reason_survives_reply_review_and_is_recorded_as_not_pending():
    item = Danmaku.from_text('给妈妈买蛋糕')
    e = _engine_with(_make_show(), [item])
    bad = proposal(e); bad['lines'].pop()
    attach(e, bad)
    ev = e._maybe_interleave_danmaku(0, '预算有限')
    dm = item.to_public()
    assert '全部剩余' in dm['reason']
    assert [x['code'] for x in dm['decision_trace']] == ['line_count', 'line_count']
    assert e.topic_evolution.decision_trace == dm['decision_trace']
    history = e.state.interaction_history[-1]
    assert history['disposition'] == 'not_adopted' and history['pending'] is False
    assert history['outcome'] == 'fallback' and history['id'] == dm['id']


def test_structural_failure_can_be_repaired_once_with_specific_reason():
    e = _engine_with(_make_show(), [])
    good = proposal(e); bad = deepcopy(good); bad['lines'].pop()
    class Repair(FakeLLM):
        def generate(self, prompt):
            if prompt.startswith('检查直播话题续写的连续性'):
                return super().generate(prompt)
            self.calls.append(prompt)
            return json.dumps(good if 'correction_required' in prompt else bad, ensure_ascii=False)
    e.topic_evolution = TopicEvolution(Repair())
    dm = Danmaku.from_text('给妈妈买蛋糕')
    e._steer_remaining_show(dm)
    assert dm.story_changed and dm.decision_trace[0]['code'] == 'line_count'
    assert dm.decision_trace[-1]['code'] == 'accepted'
