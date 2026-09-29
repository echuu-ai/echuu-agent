"""Playback has a short browser breath, never a forced 5–10 second pause."""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echuu.core.unit import AcousticHint, ScriptLine, Unit
from echuu.live.engine import EchuuLiveEngine


def _engine_with_unit(lines):
    unit = Unit(index=0, time_window=(0, 75), acoustic=AcousticHint(), lines=lines)
    eng = EchuuLiveEngine.__new__(EchuuLiveEngine)  # 跳过 __init__（不碰 LLM/TTS）
    eng.state = SimpleNamespace(show=SimpleNamespace(units=[unit]))
    return eng, unit


def test_interact_line_has_no_forced_wait():
    lines = [
        ScriptLine(id="l0", text="铺垫", stage="pad"),
        ScriptLine(id="l1", text="你们选1还是2？", stage="interact"),
    ]
    eng, unit = _engine_with_unit(lines)
    ev = eng._build_step_event(unit, 1, lines[1], None)
    assert ev["pause_after"] == 0.0


def test_unit_last_line_has_no_forced_wait():
    lines = [
        ScriptLine(id="l0", text="铺垫", stage="pad"),
        ScriptLine(id="l1", text="收尾", stage="turn"),
    ]
    eng, unit = _engine_with_unit(lines)
    ev = eng._build_step_event(unit, 1, lines[1], None)
    assert ev["pause_after"] == 0.0


def test_mid_unit_line_no_extra_pause():
    lines = [
        ScriptLine(id="l0", text="铺垫", stage="pad"),
        ScriptLine(id="l1", text="收尾", stage="turn"),
    ]
    eng, unit = _engine_with_unit(lines)
    ev = eng._build_step_event(unit, 0, lines[0], None)
    assert ev["pause_after"] == 0.0


def test_non_last_chunk_never_pauses():
    """长台词切块：只有最后一块才停顿，句中块保持连贯。"""
    lines = [ScriptLine(id="l0", text="很长的收尾台词", stage="turn")]
    eng, unit = _engine_with_unit(lines)
    ev = eng._build_step_event(unit, 0, lines[0], None, text="前半句", chunk_index=0, chunk_last=False)
    assert ev["pause_after"] == 0.0
