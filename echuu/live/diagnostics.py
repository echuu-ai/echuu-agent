"""Structured failure diagnostics for live-broadcast script generation.

When bounded, targeted repair (see echuu/generators/shareable_clip.py) still
can't clear every quality gate, the caller must not just see a bare
"clip constraints unresolved" string and move on — the specific failure
reason, the flagged lines, and every repair round's before/after content
need to land on disk so someone can look at *why* it failed. This module is
the one place that does that writing; it is deliberately independent of
EchuuLiveEngine so it can be unit-tested without standing up the whole live
pipeline.
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
    error: BaseException,
    trace: dict[str, Any],
) -> Path:
    """Write one structured diagnostic JSON file and return its path.

    `trace` is the generator's `last_trace` (see shareable_clip.generate_clip
    / legacy_v4.ScriptGeneratorV4.generate) — on failure it already carries
    `repair_rounds` (per-round before/after lines, flagged problems/issues,
    and reviews) plus the final unresolved problems. This function does not
    interpret or summarize that content; it persists it verbatim alongside
    run metadata so a human (or a follow-up automated pass) can diagnose the
    failure from disk, not just from a caught exception's message.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    unique = uuid.uuid4().hex[:8]
    filename = f"{timestamp}_{unique}_{_slug(name)}_{_slug(topic)}.json"
    path = root / filename
    payload = {
        "timestamp_utc": timestamp,
        "name": name,
        "topic": topic,
        "language": language,
        "model": model,
        "seed": seed,
        "error_type": type(error).__name__,
        "error_message": str(error),
        "error_diagnostic": getattr(error, "diagnostic", None),
        "writer_trace": trace,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
