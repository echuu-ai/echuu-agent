"""Structured generation diagnostics for live-broadcast script writing.

When bounded, targeted repair (see echuu/generators/shareable_clip.py) still
can't clear every quality gate, two different things can happen and both
need the same paper trail:

- a hard failure (no usable draft exists at all) raises, and the specific
  failure reason, flagged lines, and every repair round's before/after
  content need to land on disk instead of just a caught exception's message;
- a "closest-to-passing draft, served anyway" fallback (product decision:
  quality gating alone must never block a show from airing when a real
  draft exists) doesn't raise at all, but is exactly as important to record
  — it's a degraded outcome, not a clean pass, and should be as visible to
  ops/telemetry as a hard failure would have been.

This module is the one place that does that writing; it is deliberately
independent of EchuuLiveEngine so it can be unit-tested without standing up
the whole live pipeline.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _slug(value: str, max_len: int = 40) -> str:
    value = re.sub(r"\s+", "_", str(value or "").strip())
    value = re.sub(r"[^0-9A-Za-z一-鿿_-]", "", value)
    return value[:max_len] or "untitled"


def persist_generation_diagnostic(
    *,
    root: Path,
    name: str,
    topic: str,
    language: str,
    model: str,
    seed: Optional[int],
    trace: dict[str, Any],
    error: Optional[BaseException] = None,
    outcome: str = "failed",
) -> Path:
    """Write one structured diagnostic JSON file and return its path.

    `trace` is the generator's `last_trace` (see shareable_clip.generate_clip
    / legacy_v4.ScriptGeneratorV4.generate) — on a failure or a degraded
    fallback it already carries `repair_rounds` (per-round before/after
    lines, flagged problems/issues, and reviews) plus the final unresolved
    problems. This function does not interpret or summarize that content; it
    persists it verbatim alongside run metadata so a human (or a follow-up
    automated pass, e.g. promoting it into a regression fixture) can
    diagnose it from disk.

    Pass `error` for a hard failure (an actual raised exception propagated
    up to the caller). Omit it and pass `outcome="degraded"` for a
    closest-to-passing draft that was served anyway instead of blocking the
    show — there is no exception in that case, only a trace whose status
    reflects the fallback.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    unique = uuid.uuid4().hex[:8]
    filename = f"{timestamp}_{unique}_{_slug(name)}_{_slug(topic)}.json"
    path = root / filename
    if error is not None:
        outcome = "failed"
        error_type = type(error).__name__
        error_message = str(error)
        error_diagnostic = getattr(error, "diagnostic", None)
    else:
        error_type = trace.get("status", outcome)
        error_message = f"generation {outcome}: {trace.get('degraded_reason', '')}".strip(": ")
        error_diagnostic = trace.get("error_diagnostic")
    payload = {
        "timestamp_utc": timestamp,
        "name": name,
        "topic": topic,
        "language": language,
        "model": model,
        "seed": seed,
        "outcome": outcome,
        "error_type": error_type,
        "error_message": error_message,
        "error_diagnostic": error_diagnostic,
        "writer_trace": trace,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
