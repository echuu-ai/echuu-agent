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

import json

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
        if "DATA：" in prompt:
            data = json.loads(prompt.split("DATA：", 1)[1])
            roles = ("接住", "跟进", "回主题")
            return json.dumps({"lines": [
                f"[{roles[i]}]{data['trigger']}" for i in range(len(data['lines']))
            ]}, ensure_ascii=False)
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
    # The next three unaired lines form one bounded continuation.
    changed = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert changed == [1, 2, 3]
    assert "红包" in eng.state.show.units[0].lines[1].text
    assert note  # a real note, not silence


def test_already_aired_lines_are_never_touched():
    """current_line_in_unit=1 means lines[0] and lines[1] already played."""
    eng, llm = _make_engine(current_unit_idx=0, current_line_in_unit=1)
    dm = Danmaku(text="怎么拿出来？", user="观众A", kind="chat")
    eng._steer_remaining_show(dm)
    assert eng.state.show.units[0].lines[0].text == "原定第0-0句台词"
    assert eng.state.show.units[0].lines[1].text == "原定第0-1句台词"
    # The continuation starts strictly after the last aired line.
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
    time, with story lines between them, must not let the
    second overwrite the first's target line."""
    eng, llm = _make_engine(n_units=1, lines_per_unit=5)
    eng.state.danmaku_queue = [
        Danmaku(text="折叠屏把年终奖红包夹住了，怎么拿出来？", user="观众A", kind="chat"),
        Danmaku(text="送你一封信，提醒你先给新手机贴膜。", user="观众B", kind="gift", gift_id="信封"),
    ]
    first = eng._pick_danmaku()
    assert first.kind == "gift"  # gift outranks plain chat regardless of queue order
    eng._steer_remaining_show(first, allow_followthrough=False)
    # Simulate two lines actually being played before the next interaction.
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


def test_consecutive_batches_preserve_unaired_consequences():
    eng, _ = _make_engine(n_units=1, lines_per_unit=9)
    eng._steer_remaining_show(Danmaku(text="先给妈妈买蛋糕", user="A"))
    first_continuation = _all_line_texts(eng.state.show)[1:4]

    # A second interaction arrives after only the first rewritten line aired.
    eng.state.current_line_in_unit = 1
    eng._steer_remaining_show(Danmaku(text="再去取快递", user="B"))
    texts = _all_line_texts(eng.state.show)
    assert texts[1:4] == first_continuation
    assert all("取快递" in text for text in texts[4:7])
    assert texts[7:] == ["原定第0-7句台词", "原定第0-8句台词"]
    assert id(eng.state.show.units[0].lines[1]) not in eng.state._steered_line_ids


def test_tangent_and_batch_keep_each_others_pending_lines():
    eng, _ = _make_engine(n_units=1, lines_per_unit=10)
    eng._steer_remaining_show(Danmaku(text="先给妈妈买蛋糕", user="A"))
    eng.state.current_line_in_unit = 1
    eng._steer_remaining_show(Danmaku(
        text="", user="B", kind="tangent", entities=[{"label": "雨伞"}],
    ))
    protected = _all_line_texts(eng.state.show)[1:6]
    assert "支线" in protected[3] and "回主线" in protected[4]

    eng.state.current_line_in_unit = 2
    eng._steer_remaining_show(Danmaku(text="再去取快递", user="C"))
    texts = _all_line_texts(eng.state.show)
    assert texts[1:6] == protected
    assert all("取快递" in text for text in texts[6:9])


def test_rejected_batch_does_not_reserve_future_lines(tmp_path, monkeypatch):
    from echuu.harness.store import ArtifactStore

    eng, _ = _make_engine(n_units=1, lines_per_unit=7)
    eng._harness_directory = tmp_path
    eng._harness_runtime_store = ArtifactStore(tmp_path / "runtime", "runtime")
    eng._harness_runtime_llm = None
    eng._harness_content = {"fixture": {}}
    verdicts = iter(("needs_review", "accepted"))

    def review(text, fixture, llm, store, parent, rounds):
        return text, [], {"status": next(verdicts)}, parent

    monkeypatch.setattr("echuu.harness.repair.run_repair", review)
    original = _all_line_texts(eng.state.show)
    eng._steer_remaining_show(Danmaku(text="拒绝这次改写", user="A"))
    assert _all_line_texts(eng.state.show) == original
    assert not eng.state._steered_line_ids

    eng._steer_remaining_show(Danmaku(text="接受这次改写", user="B"))
    texts = _all_line_texts(eng.state.show)
    assert all("接受这次改写" in text for text in texts[1:4])
    assert texts[4:] == original[4:]
    assert len(eng.state._steered_line_ids) == 3


def test_replacing_show_resets_pending_continuation():
    from copy import copy

    eng, _ = _make_engine(n_units=1, lines_per_unit=7)
    eng._steer_remaining_show(Danmaku(text="旧节目方向", user="A"))
    # A new Show may reuse script objects; its reservation history is fresh.
    eng.state.show = copy(eng.state.show)
    eng._steer_remaining_show(Danmaku(text="新节目方向", user="B"))
    texts = _all_line_texts(eng.state.show)
    assert all("新节目方向" in text for text in texts[1:4])
    assert texts[4] == "原定第0-4句台词"
