"""Steering must actually rewrite unplayed script content (issue #3 in
docs/research/2026-09-11-fewshot-leakage-retest.md):
- a danmaku/gift's specific content must show up in the *actual* upcoming
  step.speech, not just get acknowledged with a queued/applied status;
- already-aired lines are never touched;
- it doesn't start an unrelated story and returns to the spine;
- a gift and a danmaku arriving together get a deterministic queue order
  and never overwrite each other's target line.
"""
from __future__ import annotations

from echuu.core.persona_model import PersonaModel
from echuu.core.story_core import StoryCore
from echuu.core.unit import AcousticHint, ScriptLine, Show, Unit
from echuu.live.state import Danmaku, PerformanceState
from echuu.live.story_steerer import StorySteerer


class _EchoLLM:
    """Returns a deterministic, inspectable rewrite so tests can assert on
    *which* trigger produced *which* line without depending on real model
    output. Records every prompt it was given."""
    def __init__(self):
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "上一句刚沿着三个线索跑了" in prompt:
            return "[回主线]沿线索讲完了"
        if "这一句必须沿着三个线索讲一小段支线" in prompt:
            entities_line = next((l for l in prompt.splitlines() if l.startswith("三个线索")), "")
            return f"[支线]{entities_line.split('：', 1)[-1]}"
        if "观众刚说/刚做" in prompt:
            trigger_line = next(
                (l for l in prompt.splitlines() if l.startswith("观众刚说/刚做：")), ""
            )
            trigger = trigger_line.split("：", 1)[-1]
            role = "跟进" if "交代一个具体结果" in prompt else "接住"
            return f"[{role}]{trigger}"
        return "改写行"


def _make_show(n_units=2, lines_per_unit=3):
    persona = PersonaModel(identity="i", belief="b", flaw="f", visual_anchor="v", title_hook="t")
    units = []
    for u in range(n_units):
        lines = [
            ScriptLine(id=f"u{u}l{i}", text=f"原定第{u}-{i}句台词", stage="pad")
            for i in range(lines_per_unit)
        ]
        units.append(Unit(
            index=u, time_window=(u * 75, (u + 1) * 75), axis="belief", density="medium",
            acoustic=AcousticHint(pitch_rate=1.0, speech_rate=1.0, volume=50),
            rupture_slots=[], lines=lines,
        ))
    story_core = StoryCore(spine="讲清楚买苹果折叠屏这件事", story_beats=("拆箱开机",))
    return Show(persona=persona, topic="年终奖买了最新的苹果折叠屏", units=units, story_core=story_core)


def _make_engine(current_unit_idx=0, current_line_in_unit=0, n_units=2, lines_per_unit=3):
    from echuu.live import engine as engmod
    eng = engmod.EchuuLiveEngine.__new__(engmod.EchuuLiveEngine)
    llm = _EchoLLM()
    eng.story_steerer = StorySteerer(llm)
    show = _make_show(n_units=n_units, lines_per_unit=lines_per_unit)
    eng.state = PerformanceState(
        name="主播", persona="人设", background="背景", topic=show.topic, show=show,
        current_unit_idx=current_unit_idx, current_line_in_unit=current_line_in_unit,
    )
    return eng, llm


def _all_line_texts(show):
    return [line.text for unit in show.units for line in unit.lines]


def test_danmaku_rewrite_lands_in_the_actual_next_unplayed_line():
    eng, llm = _make_engine(current_unit_idx=0, current_line_in_unit=0)
    before = _all_line_texts(eng.state.show)
    dm = Danmaku(text="折叠屏把年终奖红包夹住了，怎么拿出来？", user="观众A", kind="chat")
    note = eng._steer_remaining_show(dm)
    after = _all_line_texts(eng.state.show)
    # Only the two lines strictly after current_line_in_unit changed.
    changed = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert changed == [1, 2]
    assert "红包" in eng.state.show.units[0].lines[1].text
    assert note  # a real note, not silence


def test_already_aired_lines_are_never_touched():
    """current_line_in_unit=1 means lines[0] and lines[1] already played."""
    eng, llm = _make_engine(current_unit_idx=0, current_line_in_unit=1)
    dm = Danmaku(text="怎么拿出来？", user="观众A", kind="chat")
    eng._steer_remaining_show(dm)
    assert eng.state.show.units[0].lines[0].text == "原定第0-0句台词"
    assert eng.state.show.units[0].lines[1].text == "原定第0-1句台词"
    # only line[2] (and possibly the next unit's line[0]) could have changed
    assert eng.state.show.units[0].lines[2].text != "原定第0-2句台词"


def test_gift_reply_content_is_not_just_a_thank_you():
    eng, llm = _make_engine()
    dm = Danmaku(text="送你一封信，提醒你先给新手机贴膜。", user="观众B", kind="gift", gift_id="信封")
    eng._steer_remaining_show(dm)
    rewritten = eng.state.show.units[0].lines[1].text
    assert "谢谢" not in rewritten  # not a bare thank-you
    assert "贴膜" in rewritten


def test_tangent_branches_then_returns_to_the_spine_within_two_lines():
    eng, llm = _make_engine()
    dm = Danmaku(text="", user="观众C", kind="tangent", entities=[{"label": "红包"}, {"label": "折叠屏"}])
    note = eng._steer_remaining_show(dm)
    assert "回主线" in eng.state.show.units[0].lines[2].text
    assert note
    # story spine annotation reflects the interaction without discarding it
    assert "红包" in eng.state.show.story_core.story_beats[-1] or "折叠屏" in eng.state.show.story_core.story_beats[-1]


def test_followthrough_gives_the_interaction_an_actual_payoff_not_just_a_nod():
    eng, llm = _make_engine()
    dm = Danmaku(text="折叠屏把年终奖红包夹住了，怎么拿出来？", user="观众A", kind="chat")
    eng._steer_remaining_show(dm, allow_followthrough=True)
    line0, line1 = eng.state.show.units[0].lines[1].text, eng.state.show.units[0].lines[2].text
    assert "红包" in line0
    assert "红包" in line1
    assert line0 != line1  # a real second beat, not a duplicate


def test_followthrough_disabled_during_the_tight_interact_window_cadence():
    """allow_followthrough=False keeps the response to one line so a second,
    fast-cadence interaction (1-line cooldown inside an interact window)
    can't land on and overwrite an unplayed line the first steer already
    wrote to."""
    eng, llm = _make_engine()
    dm = Danmaku(text="折叠屏把年终奖红包夹住了，怎么拿出来？", user="观众A", kind="chat")
    eng._steer_remaining_show(dm, allow_followthrough=False)
    assert eng.state.show.units[0].lines[1].text != "原定第0-1句台词"
    assert eng.state.show.units[0].lines[2].text == "原定第0-2句台词"


def test_simultaneous_gift_and_danmaku_land_on_distinct_lines_in_priority_order():
    """Gift outranks plain chat in _pick_danmaku; processing them one at a
    time (as run()'s cooldown/quota naturally spaces them) must not let the
    second overwrite the first's target line."""
    eng, llm = _make_engine(n_units=1, lines_per_unit=5)
    eng.state.danmaku_queue = [
        Danmaku(text="折叠屏把年终奖红包夹住了，怎么拿出来？", user="观众A", kind="chat"),
        Danmaku(text="送你一封信，提醒你先给新手机贴膜。", user="观众B", kind="gift", gift_id="信封"),
    ]
    first = eng._pick_danmaku()
    assert first.kind == "gift"  # gift outranks plain chat regardless of queue order
    eng._steer_remaining_show(first, allow_followthrough=False)
    # Simulate two lines actually being played before the next interleave
    # opportunity (matches run()'s normal-cadence cooldown of >=2 lines).
    eng.state.current_line_in_unit = 2

    second = eng._pick_danmaku()
    assert second.kind == "chat"
    eng._steer_remaining_show(second, allow_followthrough=False)

    texts = _all_line_texts(eng.state.show)
    assert "贴膜" in texts[1]
    assert "红包" in texts[3]
    # each interaction's content landed on its own line, not on the other's
    assert "贴膜" not in texts[3]
    assert "红包" not in texts[1]
