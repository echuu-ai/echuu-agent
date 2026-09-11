"""Tests for ExampleSampler.sample_relevant — fixes the 3 root-cause bugs.

A: hard language filter no longer strands the English pool.
B: no dependence on the (dead) digressive bucket.
C: real topic/persona relevance; seed reproducible; zero-overlap degrades gracefully.
"""
from __future__ import annotations

import json

import pytest

from echuu.generators.example_sampler import ExampleSampler


@pytest.fixture
def sampler(tmp_path):
    clips = [
        {"clip_id": "z1", "language": "zh", "title": "读博换导师沉没成本",
         "notes": {"emotion": "理性到鼓励", "habit": "对吧、我觉得"},
         "transcript": [{"t": 0, "text": "沉没成本让你畏首畏尾，读博换导师其实是止损"}]},
        {"clip_id": "z2", "language": "zh", "title": "食堂打饭趣事",
         "notes": {"emotion": "轻松搞笑", "habit": "诶、那种"},
         "transcript": [{"t": 0, "text": "今天食堂阿姨手抖给我打了三块肉"}]},
        {"clip_id": "e1", "language": "en", "title": "quitting job anxiety",
         "notes": {"emotion": "vulnerable", "habit": "you know"},
         "transcript": [{"t": 0, "text": "I cried the night I decided to quit my corporate job"}]},
        {"clip_id": "e2", "language": "en", "title": "platform algorithm rant",
         "notes": {"emotion": "angry", "habit": "like"},
         "transcript": [{"t": 0, "text": "the algorithm buried my video again, so frustrating"}]},
        {"clip_id": "e3", "language": "en", "title": "academic phd sunk cost",
         "notes": {"emotion": "reflective", "habit": "i guess"},
         "transcript": [{"t": 0, "text": "switching phd advisors felt like wasting sunk cost"}]},
    ]
    f = tmp_path / "clips.jsonl"
    f.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in clips), encoding="utf-8")
    return ExampleSampler(str(f))


def test_english_pool_reachable_when_soft_language(sampler):
    """根因 A：language='zh' 但 hard_language=False → 英文 clip 可达。"""
    out = sampler.sample_relevant(
        topic="quitting my job", persona="anxious", n=5,
        language="zh", hard_language=False, temperature=0,
    )
    langs = {c["language"] for c in out}
    assert "en" in langs, "English clips must be reachable under soft language"


def test_hard_language_filters(sampler):
    """hard_language=True → 只在指定语言池里抽。"""
    out = sampler.sample_relevant(
        topic="anything", n=3, language="en", hard_language=True, temperature=0,
    )
    assert out and all(c["language"] == "en" for c in out)


def test_relevance_ranks_matching_clip_first(sampler):
    """根因 C：学术/沉没成本话题 → 相关 clip 排前（temperature=0 纯相关）。"""
    out = sampler.sample_relevant(
        topic="读博 沉没成本 换导师", persona="焦虑的研究生", n=1,
        language="zh", temperature=0,
    )
    assert out[0]["clip_id"] == "z1"


def test_relevance_english_topic(sampler):
    out = sampler.sample_relevant(
        topic="the algorithm buried my video", persona="creator", n=1,
        language="en", temperature=0,
    )
    assert out[0]["clip_id"] == "e2"


def test_seed_reproducible(sampler):
    a = sampler.sample_relevant(topic="job", n=3, language="zh", seed=42)
    b = sampler.sample_relevant(topic="job", n=3, language="zh", seed=42)
    assert [c["clip_id"] for c in a] == [c["clip_id"] for c in b]


def test_zero_overlap_does_not_crash(sampler):
    """跨语言/无重叠 → 优雅退化为近似随机，不崩。"""
    out = sampler.sample_relevant(topic="xyzqwerty 火星文", n=3, language="zh")
    assert len(out) == 3


def test_get_relevant_examples_formats(sampler):
    s = sampler.get_relevant_examples(topic="读博 沉没成本", persona="研究生", n=2, language="zh", seed=1)
    assert "真实案例" in s


def test_retrieval_excludes_current_fixture_and_reports_ids(sampler):
    formatted, ids = sampler.get_relevant_examples_with_ids(
        topic="读博 沉没成本 换导师",
        persona="焦虑的研究生",
        n=3,
        language="zh",
        seed=42,
        allowed_ids={"z1", "z2"},
        exclude_ids={"z1"},
    )
    assert ids == ["z2"]
    assert "表达统计" in formatted
    assert "食堂打饭趣事" not in formatted
    assert "读博换导师沉没成本" not in formatted


def test_allowed_pool_is_respected_before_language_fallback(sampler):
    out = sampler.sample_relevant(
        topic="anything",
        n=3,
        language="en",
        hard_language=True,
        temperature=0,
        allowed_ids={"z2", "e1"},
        exclude_ids={"e1"},
    )
    assert [clip["clip_id"] for clip in out] == ["z2"]
