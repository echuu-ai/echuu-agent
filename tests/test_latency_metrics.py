"""Roadmap item #2 (docs/research/2026-09-12-harness-and-vtuber-roadmap.md):
及时性硬指标 — plain measured numbers, not an LLM-judged score."""
from __future__ import annotations

from echuu.live.latency_metrics import danmaku_latency_seconds
from echuu.live.state import Danmaku


def test_latency_is_time_since_created_at():
    dm = Danmaku(text="x")
    dm.created_at = 100.0
    assert danmaku_latency_seconds(dm, now=102.5) == 2.5


def test_latency_never_goes_negative():
    """A clock that appears to move backward (or a `now` passed before
    creation in a test) must clamp to 0, not report a negative latency."""
    dm = Danmaku(text="x")
    dm.created_at = 100.0
    assert danmaku_latency_seconds(dm, now=90.0) == 0.0


def test_latency_defaults_to_zero_for_malformed_or_missing_timestamp():
    class NoTimestamp:
        pass

    assert danmaku_latency_seconds(NoTimestamp(), now=10.0) == 0.0

    dm = Danmaku(text="x")
    dm.created_at = "not-a-number"
    assert danmaku_latency_seconds(dm, now=10.0) == 0.0


def test_latency_uses_real_clock_when_now_omitted():
    dm = Danmaku(text="x")
    dm.created_at = 0.0  # monotonic clocks only get larger over real time
    assert danmaku_latency_seconds(dm) > 0.0
