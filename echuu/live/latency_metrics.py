"""Hard timeliness numbers (roadmap item #2, not an LLM-judged score).

Two things actually matter for "及时性" and both are just measured, not
opinions:
- how long between a danmaku/gift arriving and it actually reaching a
  played/queued response (danmaku_latency_seconds);
- how long between starting a run and the first step being playable
  (see EchuuLiveEngine.run's time_to_first_step_seconds /
  time_to_first_audio_seconds annotations).

Kept as pure functions, independent of the engine, so they can be tested
without a live session and reused anywhere a Danmaku/timestamp shows up.
"""
from __future__ import annotations

import time
from typing import Any, Optional


def danmaku_latency_seconds(dm: Any, *, now: Optional[float] = None) -> float:
    """Seconds since `dm` was created (monotonic clock), clamped at 0.

    `dm` is anything with a `created_at` float attribute (Danmaku); missing
    or malformed timestamps degrade to 0.0 rather than raising, since this
    is telemetry and must never be able to break the steering path it
    instruments.
    """
    created = getattr(dm, "created_at", None)
    if not isinstance(created, (int, float)):
        return 0.0
    if now is None:
        now = time.monotonic()
    return max(0.0, now - created)
