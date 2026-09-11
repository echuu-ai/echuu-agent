"""few-shot 密度改造：完整段落（含铺垫）+ 结构骨架进 prompt；
script_writer 目标函数从"每句可截图"改为"1-2 个高光 + 松弛铺垫"。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from echuu.generators.example_sampler import ExampleSampler
from echuu.generators.script_writer import _PROMPT_TEMPLATE


def _make_sampler(tmp_path, clips):
    p = tmp_path / "clips.jsonl"
    p.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in clips), encoding="utf-8")
    return ExampleSampler(str(p))


def _clip(n_segs=6, seg_text="这是一段很平的铺垫，没什么爆点，就是在唠嗑。"):
    return {
        "clip_id": "clip_x",
        "language": "zh",
        "title": "测试切片",
        "notes": {
            "trigger": "回应弹幕",
            "habit": "对吧",
            "structure": "共情→自我经历→对比→建议→收束",
            "emotion": "松弛",
            "feature": "大量铺垫",
        },
        "transcript": [{"t": i * 10, "text": f"[{i}]{seg_text}"} for i in range(n_segs)],
    }


def test_extract_keeps_full_transcript_in_order(tmp_path):
    """不再只挑"最有戏"的 3 句——铺垫段落要完整保留、按时间排序。"""
    sampler = _make_sampler(tmp_path, [_clip(n_segs=6)])
    text = sampler.extract_transcript_segments(sampler.clips[0])
    for i in range(6):
        assert f"[{i}]" in text
    assert text.index("[0]") < text.index("[3]") < text.index("[5]")


def test_extract_caps_at_char_budget_on_segment_boundary(tmp_path):
    sampler = _make_sampler(tmp_path, [_clip(n_segs=50)])
    text = sampler.extract_transcript_segments(sampler.clips[0], max_chars=200)
    assert 0 < len(text) <= 200 + 50  # 段边界截断，允许最后一段少量溢出换行
    assert "[0]" in text and "[49]" not in text


def test_fewshot_contains_style_without_source_facts(tmp_path):
    clip = _clip()
    marker = "海苔腰果偷吃记汇率七块变四块八LEAK_CANARY_9281"
    clip["title"] = marker
    clip["notes"] = {key: marker for key in clip["notes"]}
    clip["transcript"] = [{"t": 0, "text": marker}]
    sampler = _make_sampler(tmp_path, [clip])
    block = sampler.format_as_fewshot(sampler.clips)
    assert "表达统计" in block
    assert "松弛的铺垫" in block
    for fragment in (marker, "腰果", "汇率", "LEAK_CANARY"):
        assert fragment not in block


def test_prompt_objective_rewritten():
    """目标函数：删掉短视频文案指标，加入密度预算与弹幕禁令。"""
    assert "截图做表情包" not in _PROMPT_TEMPLATE
    assert "车轱辘话是最大的扣分项" not in _PROMPT_TEMPLATE
    assert "只设 1 个高光" in _PROMPT_TEMPLATE
    assert "最多引入 1 个新信息" in _PROMPT_TEMPLATE
    assert "不准把观众/弹幕的反应写进台词" in _PROMPT_TEMPLATE
    # 保留原有的接地气守门和结构要求
    assert "复活码" in _PROMPT_TEMPLATE
    assert "planted_gags" in _PROMPT_TEMPLATE


def test_prompt_insight_over_plot():
    """趣味来源：事要普通接地气，有意思来自表达和洞察（认知翻转，非剧情反转）。"""
    from echuu.core.persona_analyst import _PROMPT as ANALYST_PROMPT

    assert "事不用有趣，看法要有趣" in _PROMPT_TEMPLATE
    assert "认知翻转" in _PROMPT_TEMPLATE
    assert "必须炸" not in _PROMPT_TEMPLATE
    assert "认知翻转" in ANALYST_PROMPT or "把这件普通小事看穿" in ANALYST_PROMPT
    assert "洞察" in ANALYST_PROMPT
