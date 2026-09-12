"""Unit tests for echuu.live.diagnostics.persist_generation_diagnostic —
the structured, on-disk failure record for issue #1 in the 2026-09-11
retest doc (generation shouldn't fail with just a bare error string)."""
from __future__ import annotations

import json

from echuu.live.diagnostics import persist_generation_diagnostic


def test_persist_writes_one_json_file_with_full_trace(tmp_path):
    trace = {
        "status": "needs_review",
        "repair_rounds": [
            {"round": 1, "targets": ["L001"], "before_lines": [{"id": "L001", "text": "旧"}],
             "after_lines": [{"id": "L001", "text": "新"}], "structural_problems_after": [],
             "semantic_issues_after": [{"id": "L001", "quote": "新", "reason": "仍有问题"}]},
        ],
        "final_semantic_issues": [{"id": "L001", "quote": "新", "reason": "仍有问题"}],
    }
    err = ValueError("semantic repair unresolved after 1 bounded targeted rewrite round(s)")
    path = persist_generation_diagnostic(
        root=tmp_path / "diagnostics",
        name="测试主播", topic="年终奖买了最新的苹果折叠屏", language="zh",
        model="qwen3-max", seed=42, error=err, trace=trace,
    )
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "测试主播"
    assert payload["topic"] == "年终奖买了最新的苹果折叠屏"
    assert payload["model"] == "qwen3-max"
    assert payload["seed"] == 42
    assert payload["error_type"] == "ValueError"
    assert "semantic repair unresolved" in payload["error_message"]
    # The full repair-round history is on disk, not just the error string.
    assert payload["writer_trace"]["repair_rounds"] == trace["repair_rounds"]
    assert payload["writer_trace"]["final_semantic_issues"]


def test_persist_captures_diagnostic_attribute_when_present(tmp_path):
    class RichError(ValueError):
        def __init__(self, message, diagnostic):
            super().__init__(message)
            self.diagnostic = diagnostic

    err = RichError("clip constraints unresolved", diagnostic={"reason": "clip constraints unresolved", "rounds_attempted": 3})
    path = persist_generation_diagnostic(
        root=tmp_path / "diagnostics", name="n", topic="t", language="zh",
        model="m", seed=None, error=err, trace={},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["error_diagnostic"]["rounds_attempted"] == 3


def test_persist_is_safe_with_unusual_name_and_topic_characters(tmp_path):
    err = ValueError("clip constraints unresolved")
    path = persist_generation_diagnostic(
        root=tmp_path / "diagnostics", name="A/B?*<>", topic="话题 with spaces / slashes",
        language="zh", model="m", seed=1, error=err, trace={},
    )
    assert path.exists()
    assert path.parent == tmp_path / "diagnostics"


def test_persist_creates_distinct_files_for_repeated_failures(tmp_path):
    root = tmp_path / "diagnostics"
    paths = set()
    for _ in range(3):
        err = ValueError("clip constraints unresolved")
        paths.add(persist_generation_diagnostic(
            root=root, name="n", topic="t", language="zh", model="m", seed=1, error=err, trace={},
        ))
    assert len(paths) == 3
    assert len(list(root.glob("*.json"))) == 3
