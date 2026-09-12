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
        return self.reply


class RaisingLLM:
    def generate(self, prompt: str) -> str:
        raise RuntimeError("down")


class FakeTTS:
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
    e.story_steerer = type("Steerer", (), {"rewrite_line": staticmethod(lambda **_kwargs: "")})()
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


def test_emit_steering_reports_latency_since_danmaku_created():
    """Roadmap #2: latency_seconds is a plain measured number, not a score."""
    e = _engine_with(_make_show(), [])
    dm = Danmaku.from_text("你后悔吗?", user="阿强")
    dm.created_at -= 0.05  # pretend it was created slightly in the past
    payload = e.emit_steering(dm, "applied")
    assert 0.05 <= payload["latency_seconds"] < 5.0
    assert payload["item"]["status"] == "applied"


def test_run_annotates_time_to_first_step_once():
    e = _engine_with(_make_show(), [])
    events = list(e.run(max_steps=99, play_audio=False, save_audio=False))
    with_ttfs = [ev for ev in events if "time_to_first_step_seconds" in ev]
    assert len(with_ttfs) == 1
    assert with_ttfs[0] is events[0]
    assert with_ttfs[0]["time_to_first_step_seconds"] >= 0.0


def test_run_annotates_time_to_first_audio_once():
    e = _engine_with(_make_show(), [])
    events = list(e.run(max_steps=99, play_audio=False, save_audio=False))
    with_ttfa = [ev for ev in events if "time_to_first_audio_seconds" in ev]
    # FakeTTS.synthesize always returns b"audio", so the very first event
    # already has audio and should carry the annotation exactly once.
    assert len(with_ttfa) == 1
    assert with_ttfa[0] is events[0]


def test_run_interleaves_at_most_one_per_unit():
    # 给足弹幕，验证每单元最多穿插 1 条，且故事行全部播出
    danmaku = [Danmaku.from_text(f"问题{i}?", user=f"u{i}") for i in range(8)]
    e = _engine_with(_make_show(), danmaku)
    events = list(e.run(max_steps=99, play_audio=False, save_audio=False))
    story = [ev for ev in events if not ev.get("is_danmaku_response")]
    resp = [ev for ev in events if ev.get("is_danmaku_response")]
    assert len(story) == 12                 # 4 units × 3 story lines 全在
    assert 1 <= len(resp) <= 4              # 每单元最多 1 条
    # 回应事件不与故事行抢 index_in_unit
    assert all(ev["line"]["index_in_unit"] == -1 for ev in resp)
