"""Tests for danmaku interleaving — Cursor-style delivery queue + 弹幕穿插."""
from __future__ import annotations

import pytest

from echuu.core.persona_model import RichPersona
from echuu.core.unit import AcousticHint, ScriptLine, Show, Unit
from echuu.live.danmaku_interleave import DanmakuInterleaver
from echuu.live.engine import EchuuLiveEngine
from echuu.live.state import Danmaku


class FakeLLM:
    def __init__(self, reply="阿强你这个问题问得好，我当年也这样——不过先说回我那单子哈"):
        self.reply = reply
        self.calls = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if prompt.startswith("检查直播话题续写的连续性"):
            return '{"approved":true,"reason":"测试上下文连续"}'
        return self.reply


class RaisingLLM:
    def generate(self, prompt: str) -> str:
        raise RuntimeError("down")


class FakeTTS:
    enabled = True
    def update_session(self, **kw): pass
    def set_instruction(self, instruction): pass
    def synthesize(self, text, emotion_boost: float = 0.0):
        return b"audio"


# ---- DanmakuInterleaver --------------------------------------------------
def test_interleaver_returns_reply():
    di = DanmakuInterleaver(FakeLLM("阿强，问得好——回到我那单子哈"))
    out = di.respond(identity="插画师", verbal_tics=("我跟你讲",), topic="商稿",
                     last_line="改到第八版", danmaku_text="你后悔转行吗?", user="阿强")
    assert out == "阿强，问得好——回到我那单子哈"


def test_interleaver_prompt_has_context():
    llm = FakeLLM()
    di = DanmakuInterleaver(llm)
    di.respond(identity="插画师", verbal_tics=("我跟你讲",), topic="商稿稿费",
               last_line="改到第八版", danmaku_text="你后悔吗?", user="阿强")
    p = llm.calls[0]
    assert "阿强" in p and "你后悔吗?" in p and "商稿稿费" in p and "我跟你讲" in p


def test_interleaver_degrades_on_exception():
    di = DanmakuInterleaver(RaisingLLM())
    assert di.respond(identity="x", verbal_tics=(), topic="t", last_line="l",
                      danmaku_text="d", user="u") is None


def test_interleaver_empty_reply_is_none():
    di = DanmakuInterleaver(FakeLLM("   "))
    assert di.respond(identity="x", verbal_tics=(), topic="t", last_line="l",
                      danmaku_text="d", user="u") is None


# ---- engine helpers ------------------------------------------------------
def _make_show() -> Show:
    persona = RichPersona(identity="前HR现插画师", belief="b", flaw="f",
                          verbal_tics=("我跟你讲",))
    units = []
    for i in range(4):
        units.append(Unit(
            index=i, time_window=(i*75, (i+1)*75), acoustic=AcousticHint(),
            axis=None, density="high",
            lines=[ScriptLine(id=f"u{i}l{j}", text=f"u{i}故事第{j}句", stage="pad")
                   for j in range(3)],
        ))
    return Show(persona=persona, topic="商稿稿费", units=units)


def _engine_with(show, danmaku, reply="阿强我跟你讲，等我先讲完这单"):
    e = EchuuLiveEngine.__new__(EchuuLiveEngine)
    e.tts = FakeTTS()
    e.danmaku_interleaver = DanmakuInterleaver(FakeLLM(reply))
    State = type("S", (), {})
    st = State()
    st.show = show
    st.topic = show.topic
    st.name = "小梅"
    st.danmaku_queue = list(danmaku)
    st.current_unit_idx = 0
    st.current_line_in_unit = 0
    st.current_step = 0
    Mem = type("M", (), {})
    st.memory = Mem()
    st.memory.script_progress = {}
    e.state = st
    e.story_steerer = type("Steerer", (), {"rewrite_lines": staticmethod(lambda **_kwargs: None), "rewrite_line": staticmethod(lambda **_kwargs: "")})()
    e.persona_card = None
    e.gag_ledger = None
    e.on_steering = None
    e._source_material = None
    e.tuning_guidance = None
    return e


def test_pick_danmaku_prefers_sc_then_question():
    e = _engine_with(_make_show(), [
        Danmaku.from_text("普通弹幕", user="a"),
        Danmaku.from_text("你后悔吗?", user="b"),
        Danmaku.from_text("¥100 加油", user="c"),
    ])
    picked = e._pick_danmaku()
    assert picked.user == "c"  # SC 最高
    assert len(e.state.danmaku_queue) == 2


def test_maybe_interleave_builds_response_event():
    e = _engine_with(_make_show(), [Danmaku.from_text("你后悔吗?", user="阿强")])
    ev = e._maybe_interleave_danmaku(0, "改到第八版")
    assert ev is not None
    assert ev["is_danmaku_response"] is True
    assert ev["danmaku"]["user"] == "阿强"
    assert ev["line"]["stage"] == "danmaku"
    assert ev["speech"]


def test_maybe_interleave_falls_back_when_reply_empty():
    e = _engine_with(_make_show(), [Danmaku.from_text("hi", user="阿强")], reply="")
    ev = e._maybe_interleave_danmaku(0, "x")
    assert ev is not None
    assert "阿强" in ev["speech"]
    assert "hi" in ev["speech"]


def test_run_reacts_after_one_line_without_unit_quota():
    # 给足弹幕，验证第一句之后即回应，且每次互动之间仍有正文
    danmaku = [Danmaku.from_text(f"问题{i}?", user=f"u{i}") for i in range(8)]
    e = _engine_with(_make_show(), danmaku)
    events = list(e.run(max_steps=99, play_audio=False, save_audio=False))
    story = [ev for ev in events if not ev.get("is_danmaku_response")]
    resp = [ev for ev in events if ev.get("is_danmaku_response")]
    assert len(story) == 12                 # 4 units × 3 story lines 全在
    assert len(resp) == 8
    assert events[1]["is_danmaku_response"]
    assert all(not (a.get("is_danmaku_response") and b.get("is_danmaku_response")) for a,b in zip(events,events[1:]))
    # 回应事件不与故事行抢 index_in_unit
    assert all(ev["line"]["index_in_unit"] == -1 for ev in resp)


def test_topic_patch_reaches_entire_remaining_story_and_preserves_past():
    from echuu.live.topic_evolution import TopicEvolution
    import json
    e = _engine_with(_make_show(), [])
    originals = [line.text for unit in e.state.show.units for line in unit.lines]
    future = e._upcoming_script_lines()
    replacements = [{'id': line.id, 'text': '给妈妈准备她喜欢的草莓蛋糕。'} for i, (_, _, line) in enumerate(future)]
    proposal = dict(action='transition', topic='如何关心妈妈', direction='从买手机的预算转向妈妈生日安排',
                    reason='观众把自己的消费和家人的生日联系起来', lines=replacements)
    e.topic_evolution = TopicEvolution(FakeLLM(json.dumps(proposal, ensure_ascii=False)))
    note = e._steer_remaining_show(Danmaku.from_text('给妈妈买蛋糕', user='观众'))
    rendered = [ev['speech'] for ev in e._render_show()]
    assert e.state.show.units[0].lines[0].text == originals[0]
    assert rendered[1:] == [line['text'] for line in replacements]
    assert e.state.topic == e.state.show.topic == '如何关心妈妈'
    assert '剩余11句' in note


def test_malformed_batch_leaves_every_line_unchanged():
    from echuu.live.story_steerer import StorySteerer
    e = _engine_with(_make_show(), [])
    before = [line.text for unit in e.state.show.units for line in unit.lines]
    e.story_steerer = StorySteerer(FakeLLM('{"lines":["只有一句"]}'))
    e._steer_remaining_show(Danmaku.from_text('礼物', user='观众'))
    assert [line.text for unit in e.state.show.units for line in unit.lines] == before


def test_two_valid_continuation_lines_are_not_discarded():
    from echuu.live.story_steerer import StorySteerer
    steerer = StorySteerer(FakeLLM('{"lines":["选一个妈妈喜欢的蛋糕。","这比买手机更让她高兴。"]}'))
    assert len(steerer.rewrite_lines(spine='预算', topic='手机', user='观众', trigger='蛋糕', kind='chat', lines=['甲','乙','丙'])) == 2
