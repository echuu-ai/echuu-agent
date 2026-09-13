"""When _create_legacy_v4_performance's writer exhausts its bounded repair
rounds and raises, the engine must persist a structured diagnostic to disk
and let the error propagate cleanly — not hang, and not swallow the failure
into a silently-degraded show. See docs/research/2026-09-11-fewshot-leakage-retest.md
issue #1 and echuu/live/diagnostics.py.
"""
from __future__ import annotations

import json

import pytest


class _FakeLLMGen:
    """Stands in for the `.generate(prompt)`-adapted LLM client engine.py
    expects on `self.llm_gen`; _create_legacy_v4_performance only reads
    `.calls` and `.model` before handing off to the (also faked) generator."""
    def __init__(self):
        self.calls = []
        self.model = "fake-model"


class _FailingGenerator:
    """Stands in for ScriptGeneratorV4: always raises with a rich diagnostic
    already on its own last_trace, exactly like shareable_clip.generate_clip
    does after exhausting its bounded repair rounds."""
    def __init__(self, llm, example_sampler=None):
        self.llm = llm
        self.last_trace = {
            "status": "needs_review",
            "rounds_attempted": 2,
            "max_repair_rounds": 2,
            "repair_rounds": [
                {"round": 1, "targets": ["L001"], "wide": False,
                 "before_lines": [{"id": "L001", "text": "旧台词"}],
                 "after_lines": [{"id": "L001", "text": "改过的台词"}],
                 "structural_problems_after": [], "semantic_issues_after": [{"id": "L001", "quote": "改过的台词", "reason": "仍与主题冲突"}]},
                {"round": 2, "targets": ["L001"], "wide": False,
                 "before_lines": [{"id": "L001", "text": "改过的台词"}],
                 "after_lines": [{"id": "L001", "text": "再改一次"}],
                 "structural_problems_after": [], "semantic_issues_after": [{"id": "L001", "quote": "再改一次", "reason": "仍与主题冲突"}]},
            ],
            "final_semantic_issues": [{"id": "L001", "quote": "再改一次", "reason": "仍与主题冲突"}],
        }

    def generate(self, **kwargs):
        from echuu.generators.shareable_clip import ClipGenerationError
        raise ClipGenerationError(
            "semantic repair unresolved after 2 bounded targeted rewrite round(s)",
            diagnostic={"reason": "semantic repair unresolved", **self.last_trace},
        )


def _make_engine(monkeypatch, tmp_path):
    from echuu.live import engine as engmod
    monkeypatch.setattr(engmod, "ScriptGeneratorV4", _FailingGenerator)
    eng = engmod.EchuuLiveEngine.__new__(engmod.EchuuLiveEngine)
    eng.project_root = tmp_path
    eng.llm_gen = _FakeLLMGen()
    eng.llm = object()  # not a QwenClient instance -> clip_adapter stays llm_gen
    eng.example_sampler = None
    eng.persona_card = None
    eng.gag_ledger = None
    eng.dossier = None
    eng._dossier_cache_hit = False
    eng.state = None
    return eng, engmod


def test_failed_generation_persists_diagnostic_and_reraises(monkeypatch, tmp_path):
    eng, engmod = _make_engine(monkeypatch, tmp_path)
    from echuu.generators.shareable_clip import ClipGenerationError

    with pytest.raises(ClipGenerationError, match="semantic repair unresolved"):
        eng._create_legacy_v4_performance(
            name="主播", persona="人设", background="年终奖买了最新的苹果折叠屏",
            topic="年终奖买了最新的苹果折叠屏", language="zh", character_config=None,
            on_phase_callback=None, generation_seed=7,
        )

    diagnostics_dir = tmp_path / "output" / "diagnostics"
    files = list(diagnostics_dir.glob("*.json"))
    assert len(files) == 1, "exactly one diagnostic file should be written per failed attempt"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["topic"] == "年终奖买了最新的苹果折叠屏"
    assert payload["seed"] == 7
    assert payload["error_type"] == "ClipGenerationError"
    assert payload["writer_trace"]["rounds_attempted"] == 2
    assert len(payload["writer_trace"]["repair_rounds"]) == 2
    # The engine's own trace surface also reflects the failure, for any
    # in-process caller that catches the exception instead of reading disk.
    assert eng._legacy_writer_trace["status"] == "needs_review"


def test_degraded_generation_persists_diagnostic_and_does_not_raise(monkeypatch, tmp_path):
    """Product decision: quality gating alone must never block a show from
    airing when a real draft exists. generate() serving a closest-to-passing
    draft instead of raising must still leave the same paper trail a hard
    failure would have — just tagged 'degraded', not 'failed' — and must not
    itself raise."""
    from echuu.generators.legacy_v4 import ScriptLineV4

    class _DegradedGenerator:
        def __init__(self, llm, example_sampler=None):
            self.llm = llm
            self.last_trace = {
                "status": "completed_degraded",
                "degraded_reason": "clip constraints unresolved",
                "rounds_attempted": 2,
                "repair_rounds": [{"round": 1, "before_lines": [], "after_lines": []}],
                "final_structural_problems": [{"code": "duration", "chars": 40}],
                "final_semantic_issues": [],
            }

        def generate(self, **kwargs):
            return [ScriptLineV4(id="L001", text="最接近达标但没完全通过的台词", stage="Hook", interruption_cost=0.4)]

    eng, engmod = _make_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(engmod, "ScriptGeneratorV4", _DegradedGenerator)
    try:
        eng._create_legacy_v4_performance(
            name="主播", persona="人设", background="年终奖买了最新的苹果折叠屏",
            topic="年终奖买了最新的苹果折叠屏", language="zh", character_config=None,
            on_phase_callback=None, generation_seed=7,
        )
    except Exception:
        pass  # downstream unit-planning isn't stubbed here; only the diagnostic write matters

    diagnostics_dir = tmp_path / "output" / "diagnostics"
    files = list(diagnostics_dir.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["outcome"] == "degraded"
    assert payload["writer_trace"]["status"] == "completed_degraded"
    assert payload["writer_trace"]["rounds_attempted"] == 2
    assert eng._legacy_writer_trace["status"] == "completed_degraded"


def test_successful_generation_does_not_write_a_diagnostic(monkeypatch, tmp_path):
    """Diagnostics are for failures; a clean run shouldn't litter output/."""
    from echuu.live import engine as engmod
    from echuu.generators.legacy_v4 import ScriptLineV4

    class _SucceedingGenerator:
        def __init__(self, llm, example_sampler=None):
            self.llm = llm
            self.last_trace = {"status": "completed"}

        def generate(self, **kwargs):
            return [ScriptLineV4(id="L001", text="一句正常台词", stage="Hook", interruption_cost=0.4)]

    monkeypatch.setattr(engmod, "ScriptGeneratorV4", _SucceedingGenerator)
    # _create_legacy_v4_performance does real work after generate() succeeds
    # (unit planning, sanitization, Show assembly) that this test doesn't
    # need to exercise — it only needs to prove no diagnostic file appears,
    # so a failure downstream of generate() is fine to let propagate; what
    # matters is output/diagnostics/ stays empty either way.
    eng, _ = _make_engine(monkeypatch, tmp_path)
    monkeypatch.setattr(engmod, "ScriptGeneratorV4", _SucceedingGenerator)
    try:
        eng._create_legacy_v4_performance(
            name="主播", persona="人设", background="背景",
            topic="话题", language="zh", character_config=None,
            on_phase_callback=None, generation_seed=7,
        )
    except Exception:
        pass
    diagnostics_dir = tmp_path / "output" / "diagnostics"
    assert not diagnostics_dir.exists() or not list(diagnostics_dir.glob("*.json"))
