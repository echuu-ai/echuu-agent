"""Tests for engine unit-by-unit rendering and acoustic switching."""
from __future__ import annotations

import pytest

from echuu.core.persona_model import PersonaModel
from echuu.core.unit import AcousticHint, Rupture, ScriptLine, Show, Unit


class FakeTTS:
    enabled = True
    def __init__(self):
        self.update_calls: list[dict] = []
        self.synth_calls: list[str] = []
        self.fail_on_text: str | None = None

    def update_session(self, *, pitch_rate, speech_rate, volume):
        self.update_calls.append({
            "pitch_rate": pitch_rate, "speech_rate": speech_rate, "volume": volume,
        })

    def set_instruction(self, instruction: str):
        pass

    def synthesize(self, text: str, emotion_boost: float = 0.0):
        self.synth_calls.append(text)
        if self.fail_on_text and self.fail_on_text in text:
            return None
        return b"fake-audio"


def _make_show() -> Show:
    persona = PersonaModel(identity="i", belief="b", flaw="f", visual_anchor="v", title_hook="t")
    units = []
    for i in range(4):
        u = Unit(
            index=i,
            time_window=(i*75, (i+1)*75),
            axis="identity" if i == 0 else ("belief" if i < 3 else "flaw"),
            density="high" if i < 2 else ("medium" if i == 2 else "low"),
            acoustic=AcousticHint(pitch_rate=1.0 + 0.05 * (1 if i % 2 == 0 else -1),
                                  speech_rate=1.0, volume=50),
            rupture_slots=[Rupture(kind="flaw" if i == 3 else "meme",
                                   nucleus_mode="tiny_shame", intensity=0.5 + 0.1 * i)],
            lines=[
                ScriptLine(id=f"u{i}l0", text=f"u{i} hook", stage="hook"),
                ScriptLine(id=f"u{i}l1", text=f"u{i} pad",  stage="pad"),
                ScriptLine(id=f"u{i}l2", text=f"u{i} turn", stage="turn", is_rupture=(i == 3)),
            ],
        )
        units.append(u)
    return Show(persona=persona, topic="t", units=units)


def test_render_calls_update_session_once_per_unit(monkeypatch):
    from echuu.live.engine import EchuuLiveEngine
    engine = EchuuLiveEngine.__new__(EchuuLiveEngine)  # bypass __init__
    engine.tts = FakeTTS()
    engine.danmaku_handler = None
    engine.state = type("S", (), {"show": _make_show(), "memory": None})()

    events = list(engine._render_show())
    assert len(engine.tts.update_calls) == 4, "exactly one update_session per unit"


def test_render_update_kwargs_match_unit_acoustic(monkeypatch):
    from echuu.live.engine import EchuuLiveEngine
    engine = EchuuLiveEngine.__new__(EchuuLiveEngine)
    engine.tts = FakeTTS()
    engine.danmaku_handler = None
    show = _make_show()
    engine.state = type("S", (), {"show": show, "memory": None})()

    list(engine._render_show())
    for i, call in enumerate(engine.tts.update_calls):
        assert call["pitch_rate"]  == show.units[i].acoustic.pitch_rate
        assert call["speech_rate"] == show.units[i].acoustic.speech_rate
        assert call["volume"]      == show.units[i].acoustic.volume


def test_render_yields_step_event_per_line(monkeypatch):
    from echuu.live.engine import EchuuLiveEngine
    engine = EchuuLiveEngine.__new__(EchuuLiveEngine)
    engine.tts = FakeTTS()
    engine.danmaku_handler = None
    engine.state = type("S", (), {"show": _make_show(), "memory": None})()

    events = list(engine._render_show())
    assert len(events) == 4 * 3  # 4 units × 3 lines
    for ev in events:
        assert "unit" in ev and "line" in ev
        assert ev["unit"]["index"] in (0, 1, 2, 3)
        assert ev["line"]["stage"] in ("hook", "pad", "turn")


def test_render_reports_failure_when_enabled_tts_returns_none(monkeypatch):
    from echuu.live.engine import EchuuLiveEngine
    engine = EchuuLiveEngine.__new__(EchuuLiveEngine)
    engine.tts = FakeTTS()
    engine.tts.fail_on_text = "u1 pad"
    engine.danmaku_handler = None
    engine.state = type("S", (), {"show": _make_show(), "memory": None})()

    with pytest.raises(RuntimeError, match="语音生成失败"):
        list(engine._render_show())
