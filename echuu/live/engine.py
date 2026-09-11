"""
echuu 实时引擎（解耦版，支持多语言）。
支持 MP3 转换和实时流式播放。
"""

from __future__ import annotations

import json
import os
import random
import hashlib
from collections import OrderedDict, defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Dict, List, Optional

from dotenv import load_dotenv

from ..core.pattern_analyzer import PatternAnalyzer
from ..core.persona_model import PersonaModel, RichPersona
from ..core.persona_analyst import PersonaAnalyst, PROMPT_VERSION as ANALYST_PROMPT_VERSION
from ..core.persona_expander import PersonaExpander, PROMPT_VERSION as DOSSIER_PROMPT_VERSION
from ..core.persona_dossier import CharacterDossier
from ..core.prompt_leakage import find_prompt_leaks
from ..core.output_safety import sanitize_audience_text
from ..core.story_core import StoryCore
from ..core.unit import ScriptLine, Show
from ..core.unit_planner import UnitPlanner
from ..core.structure_breaker import StructureBreaker
from ..core.tuning_guidance import (
    normalize_tuning_guidance,
    render_tuning_guidance,
    tuning_guidance_categories,
    tuning_guidance_fingerprint,
    tuning_guidance_ids,
)
from ..generators.example_sampler import ExampleSampler
from ..generators.script_writer import ScriptWriter, PROMPT_VERSION as WRITER_PROMPT_VERSION
from ..generators.legacy_v4 import ScriptGeneratorV4, adapt_duration_config
from .danmaku import DanmakuEvaluator, DanmakuHandler
from .danmaku_interleave import DanmakuInterleaver
from .story_steerer import StorySteerer, annotate_remaining_beats
from .llm_factory import create_llm_client
from .performer import PerformerV3
from .state import Danmaku, PerformanceState, PerformerMemory
from .tts_client import TTSClient
from .language import setup_stream_language_from_topic, StreamLanguageContext
from .motion import pick_motion_for_danmaku, pick_motion_for_step
from ..harness.runtime import trace_runtime, guard_steering, approve_reply
from .audio_player import StreamSimulator


_DOSSIER_CACHE_VERSION = "v2-soft-conflict"
_DOSSIER_CACHE_MAX_SIZE = 128
_DOSSIER_CACHE: "OrderedDict[str, CharacterDossier]" = OrderedDict()
_DOSSIER_CACHE_LOCK = Lock()
_LEGACY_RANDOM_LOCK = Lock()


def _dossier_cache_key(
    name: str,
    persona: str,
    background: str,
    card,
    tuning_guidance=None,
) -> str:
    card_text = card.render_for_prompt() if card and not card.is_empty() else ""
    payload = "\0".join((
        _DOSSIER_CACHE_VERSION,
        name or "",
        persona or "",
        background or "",
        card_text,
        tuning_guidance_fingerprint(tuning_guidance),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _get_cached_dossier(key: str) -> Optional[CharacterDossier]:
    with _DOSSIER_CACHE_LOCK:
        dossier = _DOSSIER_CACHE.get(key)
        if dossier is not None:
            _DOSSIER_CACHE.move_to_end(key)
        return dossier


def _cache_dossier(key: str, dossier: CharacterDossier) -> None:
    with _DOSSIER_CACHE_LOCK:
        _DOSSIER_CACHE[key] = dossier
        _DOSSIER_CACHE.move_to_end(key)
        while len(_DOSSIER_CACHE) > _DOSSIER_CACHE_MAX_SIZE:
            _DOSSIER_CACHE.popitem(last=False)


def _find_project_root() -> Path:
    root = Path.cwd()
    while root.name != "echuu-agent" and root.parent != root:
        root = root.parent
    return root


class _LLMGenerateAdapter:
    """Bridge the engine's LLM client (`.call(prompt, system=, max_tokens=)`)
    to the `.generate(prompt) -> str` contract that PersonaModel and
    ScriptWriter / PersonaAnalyst expect.

    The V5 modules were unit-tested against a `.generate` stub; real clients
    (Gemini / Claude / OpenAI) only expose `.call`. This adapter keeps the
    modules client-agnostic without leaking the LLM interface into them.
    """

    def __init__(self, client, max_tokens: int = 8000) -> None:
        self._client = client
        self._max_tokens = max_tokens
        self.provider = client.__class__.__name__
        self.model = str(getattr(client, "model", "") or "unknown")
        self.calls: list[dict] = []

    def generate(self, prompt: str) -> str:
        started = perf_counter()
        call = {
            "provider": self.provider,
            "model": self.model,
            "prompt_chars": len(prompt or ""),
            "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()[:16],
            "max_tokens": self._max_tokens,
        }
        try:
            # Prefer a native .generate if a future client provides one.
            native = getattr(self._client, "generate", None)
            if callable(native):
                response = native(prompt)
            else:
                response = self._client.call(prompt, max_tokens=self._max_tokens)
            call.update({
                "status": "completed",
                "response_chars": len(response or ""),
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            })
            return response
        except Exception as exc:
            call.update({
                "status": "failed",
                "response_chars": 0,
                "duration_ms": round((perf_counter() - started) * 1000, 1),
                "error_type": exc.__class__.__name__,
            })
            raise
        finally:
            self.calls.append(call)

    def call(self, prompt: str, system: str | None = None, max_tokens: int | None = None) -> str:
        """Expose the historical V4 client contract while retaining telemetry."""
        started = perf_counter()
        token_limit = int(max_tokens or self._max_tokens)
        call = {
            "provider": self.provider,
            "model": self.model,
            "prompt_chars": len(prompt or "") + len(system or ""),
            "prompt_sha256": hashlib.sha256(
                ((system or "") + "\0" + (prompt or "")).encode("utf-8")
            ).hexdigest()[:16],
            "max_tokens": token_limit,
        }
        try:
            response = self._client.call(prompt, system=system, max_tokens=token_limit)
            call.update({
                "status": "completed",
                "response_chars": len(response or ""),
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            })
            return response
        except Exception as exc:
            call.update({
                "status": "failed",
                "response_chars": 0,
                "duration_ms": round((perf_counter() - started) * 1000, 1),
                "error_type": exc.__class__.__name__,
            })
            raise
        finally:
            self.calls.append(call)

    def summarize_since(self, start_index: int) -> dict:
        calls = self.calls[start_index:]
        prompt_sizes = [int(call.get("prompt_chars", 0)) for call in calls]
        return {
            "uses_ai": bool(calls),
            "provider": self.provider,
            "model": self.model,
            "call_count": len(calls),
            "duration_ms": round(sum(float(call.get("duration_ms", 0)) for call in calls), 1),
            "prompt_chars": sum(int(call.get("prompt_chars", 0)) for call in calls),
            "avg_prompt_chars": (
                round(sum(prompt_sizes) / len(prompt_sizes), 1)
                if prompt_sizes else 0
            ),
            "max_prompt_chars": max(prompt_sizes, default=0),
            "response_chars": sum(int(call.get("response_chars", 0)) for call in calls),
            "failed_calls": sum(call.get("status") == "failed" for call in calls),
            "calls": calls,
        }


class EchuuLiveEngine:
    """
    echuu 实时直播引擎（整合版）。

    - Phase 1: 预生成完整剧本（默认使用 V4.1）
    - Phase 2: 实时表演 + 记忆系统 + 弹幕互动
    """

    def __init__(self, data_path: Optional[str] = None, llm_provider: Optional[str] = None):
        """
        初始化 echuu 实时引擎。

        Args:
            data_path: 数据文件路径（可选）。
            llm_provider: LLM 提供商 ("gemini", "claude", "openai")。
                          如果未指定，根据可用的 API Key 自动选择。
        """
        self.project_root = _find_project_root()
        load_dotenv(self.project_root / ".env")

        self.llm = create_llm_client(provider=llm_provider)
        self.tts = TTSClient()

        self.analyzer = None
        data_file = Path(data_path) if data_path else self.project_root / "data" / "annotated_clips.json"
        if data_file.exists():
            with data_file.open("r", encoding="utf-8") as f:
                clips = json.load(f)
            self.analyzer = PatternAnalyzer(clips)

        clips_file = self.project_root / "data" / "vtuber_raw_clips_for_notebook_full_30_cleaned.jsonl"
        self.example_sampler = ExampleSampler(str(clips_file)) if clips_file.exists() else None

        # V5 modules expect a `.generate(prompt)` LLM contract; real clients
        # expose `.call(...)`. Adapt once, here, at the engine boundary.
        self.llm_gen = _LLMGenerateAdapter(self.llm)

        self.persona_analyst = PersonaAnalyst()
        self.persona_expander = PersonaExpander()
        self.dossier: Optional[CharacterDossier] = None
        self._dossier_cache_hit = False
        self.tuning_guidance: tuple[dict[str, str], ...] = ()
        self.debug_trace: dict = {"version": 1, "stages": []}
        self.unit_planner = UnitPlanner()
        self.structure_breaker = StructureBreaker()
        # Phase 2-①：用户人设卡 + call-back 埋梗账本（create_performance 里填充）
        self.persona_card = None
        from ..core.gag_ledger import GagLedger as _GagLedger
        self.gag_ledger = _GagLedger()
        self.script_writer = ScriptWriter(self.llm_gen, self.example_sampler)
        self.danmaku_handler = DanmakuHandler(DanmakuEvaluator())
        self.danmaku_interleaver = DanmakuInterleaver(self.llm_gen)
        self.story_steerer = StorySteerer(self.llm_gen)
        self.on_steering = None
        self.on_token_hunt = None
        self.performer = PerformerV3(self.llm, self.tts, self.danmaku_handler)

        self.scripts_dir = self.project_root / "output" / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

        self.state: Optional[PerformanceState] = None
        self.stream_lang_context: Optional[StreamLanguageContext] = None

    def setup(
        self,
        name: str,
        persona: str,
        topic: str,
        background: str = "",
        language: str = "zh",
        character_config: Optional[dict] = None,
        on_phase_callback: Optional[callable] = None,
        persona_card: Optional[dict] = None,
        enable_dossier: bool = False,
        story_pipeline: Optional[str] = None,
        generation_seed: Optional[int] = None,
        retrieval_allowed_ids: Optional[List[str]] = None,
        retrieval_exclude_ids: Optional[List[str]] = None,
        retrieval_seed: Optional[int] = None,
        tuning_guidance: Optional[List[dict]] = None,
        tone_intent: str = "auto",
        depth_budget: str = "auto",
        entertainment_mechanism: str = "",
    ) -> PerformanceState:
        """设置表演参数并生成剧本。"""
        # 在这里我们可以捕获推理过程并传给回调
        self.state = self.create_performance(
            name=name,
            persona=persona,
            background=background,
            topic=topic,
            language=language,
            character_config=character_config,
            on_phase_callback=on_phase_callback,
            persona_card=persona_card,
            enable_dossier=enable_dossier,
            story_pipeline=story_pipeline,
            generation_seed=generation_seed,
            retrieval_allowed_ids=retrieval_allowed_ids,
            retrieval_exclude_ids=retrieval_exclude_ids,
            retrieval_seed=retrieval_seed,
            tuning_guidance=tuning_guidance,
            tone_intent=tone_intent,
            depth_budget=depth_budget,
            entertainment_mechanism=entertainment_mechanism,
        )
        print(f"\n表演设置完成: {name} - {topic}")
        print(f"剧本行数: {len(self.state.script_lines)}")
        return self.state

    def _maybe_expand(
        self,
        name,
        persona,
        background,
        enable_dossier: bool,
        tuning_guidance=None,
    ):
        """按开关扩充人物小传；相同人设在进程内复用，避免重复 LLM 调用。"""
        if not enable_dossier:
            self._dossier_cache_hit = False
            return None
        cache_key = _dossier_cache_key(
            name, persona, background, self.persona_card, tuning_guidance,
        )
        cached = _get_cached_dossier(cache_key)
        if cached is not None:
            self._dossier_cache_hit = True
            return cached
        self._dossier_cache_hit = False
        try:
            dossier = self.persona_expander.expand(
                name, persona, background, self.persona_card, self.llm_gen,
                tuning_guidance=tuning_guidance,
            )
            if dossier is not None and not dossier.is_empty():
                _cache_dossier(cache_key, dossier)
            return dossier
        except Exception as exc:  # noqa: BLE001 — 扩充失败不阻塞开播
            print(f"[engine] 人物小传扩充失败（跳过）: {exc}")
            return None

    def _create_legacy_v4_performance(
        self,
        *,
        name: str,
        persona: str,
        background: str,
        topic: str,
        language: str,
        character_config: Optional[dict],
        on_phase_callback: Optional[callable],
        generation_seed: Optional[int],
    ) -> PerformanceState:
        """Run the pre-refactor V4 writer and adapt its lines to today's Show."""
        from echuu.core.gag_ledger import GagLedger

        self.dossier = None
        self._dossier_cache_hit = False
        self.gag_ledger = GagLedger()
        seed = int(generation_seed if generation_seed is not None else random.randrange(2**32))
        if on_phase_callback:
            on_phase_callback("Legacy V4: 筛选事件并生成 1–2 分钟角色 clip...")
        started = perf_counter()
        call_start = len(self.llm_gen.calls)
        generator = ScriptGeneratorV4(self.llm_gen, self.example_sampler)
        writer_config = dict(character_config or {})
        writer_config.setdefault("output_format", "shareable_clip" if language.startswith("zh") else "casual")
        voice_key = hashlib.sha256((name + "\n" + persona + "\n" + language).encode()).hexdigest()
        voice_cache = getattr(self, "_legacy_voice_designs", {})
        if voice_key in voice_cache:
            writer_config.setdefault("voice_design", voice_cache[voice_key])
        if self.persona_card and "speech_tics" not in writer_config:
            writer_config["speech_tics"] = list(self.persona_card.speech_tics)
        # Adapt subsequent requests only when no explicit duration was requested.
        previous = getattr(self, "_legacy_writer_trace", {})
        if writer_config["output_format"] == "shareable_clip":
            writer_config.setdefault("target_seconds", 90)
            writer_config.setdefault("candidate_count", 1 if previous.get("generation_seconds", 0) > 45 else 2)
        else:
            writer_config = adapt_duration_config(writer_config, previous)
        # Keep the legacy pipeline contract; use the validated clip writer model on Qwen.
        clip_adapter = self.llm_gen
        if writer_config["output_format"] == "shareable_clip":
            from .qwen_client import QwenClient
            if isinstance(self.llm, QwenClient):
                clip_model = writer_config.get("clip_model") or os.getenv("ECHUU_CLIP_MODEL", "qwen3-max")
                if clip_model != self.llm.model:
                    clip_client = QwenClient(api_key=self.llm.api_key, model=clip_model)
                    clip_client.seed = seed
                    clip_adapter = _LLMGenerateAdapter(clip_client)
                    generator = ScriptGeneratorV4(clip_adapter, self.example_sampler)
        # The revised writer is deterministic apart from the seeded provider call;
        # do not serialize network requests with the old process-global RNG lock.
        self.state = None
        try:
            legacy_lines = generator.generate(
                name=name, persona=persona, background=background, topic=topic,
                language=language, character_config=writer_config,
            )
        finally:
            self._legacy_writer_trace = getattr(generator, "last_trace", {})
            self._legacy_writer_trace["model"] = clip_adapter.model
            if clip_adapter is not self.llm_gen:
                self.llm_gen.calls.extend(clip_adapter.calls)
        if self._legacy_writer_trace.get("status") == "completed":
            design = self._legacy_writer_trace.get("plan", {}).get("voice_design")
            if design:
                voice_cache[voice_key] = design
                self._legacy_voice_designs = voice_cache
        source_material = {
            "name": name,
            "persona": persona,
            "background": background,
            "topic": topic,
        }
        safety_issues: list[str] = []
        safe_lines = []
        for line in legacy_lines:
            result = sanitize_audience_text(
                line.text,
                source_material=source_material,
                tuning_guidance=self.tuning_guidance,
            )
            safety_issues.extend(result.issues)
            if result.text:
                line.text = result.text
                safe_lines.append(line)

        n_units = max(2, min(4, (len(safe_lines) + 2) // 3))
        units = self.unit_planner.plan(n_units)
        for index, legacy_line in enumerate(safe_lines):
            unit_index = min(n_units - 1, index * n_units // max(1, len(safe_lines)))
            unit = units[unit_index]
            line_index = len(unit.lines)
            unit.lines.append(ScriptLine(
                id=legacy_line.id or f"u{unit_index}l{line_index}",
                text=legacy_line.text,
                stage="hook" if index == 0 else "pad",
                is_rupture=False,
                interruption_cost=float(legacy_line.interruption_cost),
                key_info=list(getattr(legacy_line, "key_info", None) or []),
            ))
        for unit in units:
            if unit.lines:
                unit.lines[-1].stage = "turn"
        if units and units[0].lines:
            units[0].lines[0].stage = "hook"

        verbal_tics = tuple(getattr(self.persona_card, "speech_tics", ()) or ())
        rich_persona = RichPersona(
            identity=(persona or name).strip(),
            belief="",
            flaw="保留真实的不完美和临场反应",
            verbal_tics=verbal_tics,
        )
        show = Show(persona=rich_persona, topic=topic, units=units, story_core=None)
        flat_lines = [line for unit in units for line in unit.lines]
        self.debug_trace["stages"].append({
            "id": "legacy_v4_generation",
            "label": "Legacy V4 故事生成",
            "status": "degraded" if safety_issues else "completed",
            "duration_ms": round((perf_counter() - started) * 1000, 1),
            "ai": self.llm_gen.summarize_since(call_start),
            "input": {"seed": seed, **source_material},
            "metrics": {
                "seed": seed,
                "line_count": len(flat_lines),
                "unit_count": len(units),
                "safety_issue_count": len(set(safety_issues)),
                "safety_issues": list(dict.fromkeys(safety_issues)),
                "dossier_enabled": False,
                "writer_trace": self._legacy_writer_trace,
            },
            "output": [asdict(unit) for unit in units],
        })

        catchphrases = []
        if self.analyzer:
            catchphrases = [cp for cp, _ in self.analyzer.extract_catchphrases(language)[:5]]
        memory = PerformerMemory()
        memory.script_progress["total_lines"] = len(flat_lines)
        memory.script_progress["current_stage"] = flat_lines[0].stage if flat_lines else "hook"
        self.performer = PerformerV3(
            self.llm,
            self.tts,
            self.danmaku_handler,
            stream_lang_context=self.stream_lang_context,
        )
        self._save_script_v5(show, name, topic)
        self._print_script_preview_v5(show)
        return PerformanceState(
            name=name,
            persona=persona,
            background=background,
            topic=topic,
            show=show,
            script_lines=flat_lines,
            memory=memory,
            catchphrases=catchphrases,
        )

    def create_performance(
        self,
        name: str,
        persona: str,
        background: str,
        topic: str,
        language: str = "zh",
        character_config: Optional[dict] = None,
        on_phase_callback: Optional[callable] = None,
        persona_card: Optional[dict] = None,
        enable_dossier: bool = False,
        story_pipeline: Optional[str] = None,
        generation_seed: Optional[int] = None,
        retrieval_allowed_ids: Optional[List[str]] = None,
        retrieval_exclude_ids: Optional[List[str]] = None,
        retrieval_seed: Optional[int] = None,
        tuning_guidance: Optional[List[dict]] = None,
        tone_intent: str = "auto",
        depth_budget: str = "auto",
        entertainment_mechanism: str = "",
    ) -> PerformanceState:
        """Generate a show with legacy V4 by default or the refactored pipeline."""
        from echuu.core.persona_card import PersonaCard

        pipeline = str(
            story_pipeline or os.getenv("ECHUU_STORY_PIPELINE", "legacy_v4")
        ).strip().lower()
        self._harness_directory = None
        self._harness_content = None
        if pipeline not in {"legacy_v4", "refactor", "harness_story_v1"}:
            raise ValueError(f"unsupported story pipeline: {pipeline}")
        if hasattr(self.llm, "seed"):
            self.llm.seed = generation_seed
        self.tuning_guidance = normalize_tuning_guidance(tuning_guidance)
        self._source_material = {
            "name": name,
            "persona": persona,
            "background": background,
            "topic": topic,
        }
        guidance_ids = tuning_guidance_ids(self.tuning_guidance)
        guidance_categories = tuning_guidance_categories(self.tuning_guidance)
        self.debug_trace = {
            "version": 1,
            "pipeline": pipeline,
            "stages": [{
                "id": "input",
                "label": "输入",
                "status": "completed",
                "duration_ms": 0,
                "ai": {"uses_ai": False, "call_count": 0},
                "output": {
                    "name": name,
                    "persona": persona,
                    "background": background,
                    "topic": topic,
                    "language": language,
                    "enable_dossier": enable_dossier,
                    "story_pipeline": pipeline,
                    "generation_seed": generation_seed,
                    "persona_card": persona_card,
                    "retrieval_allowed_ids": retrieval_allowed_ids,
                    "retrieval_exclude_ids": retrieval_exclude_ids,
                    "retrieval_seed": retrieval_seed,
                    "tuning_guidance_ids": guidance_ids,
                    "tone_intent": tone_intent,
                    "depth_budget": depth_budget,
                    "entertainment_mechanism": entertainment_mechanism,
                },
            }],
        }
        self.debug_trace["stages"].append({
            "id": "tuning_guidance",
            "label": "人类批注规则",
            "status": "completed" if self.tuning_guidance else "skipped",
            "duration_ms": 0,
            "ai": {"uses_ai": False, "call_count": 0},
            "input": {
                "source": "TRAIN tuning notes",
                "note_ids": guidance_ids,
            },
            "metrics": {
                "rule_count": len(self.tuning_guidance),
                "categories": guidance_categories,
                "fingerprint": tuning_guidance_fingerprint(self.tuning_guidance),
            },
            "output": {
                "rules": list(self.tuning_guidance),
                "prompt_text": render_tuning_guidance(self.tuning_guidance),
            },
        })
        self.stream_lang_context = setup_stream_language_from_topic(topic, persona)
        print(f"🌐 语言设置: {self.stream_lang_context.greeting_style}")

        # 用户人设卡（可选）：口癖/称呼/雷点等硬约束，贯穿 analyst/writer/弹幕回应
        self.persona_card = PersonaCard.from_dict(persona_card)
        if self.persona_card:
            print(f"🎴 人设卡已加载: 口癖={list(self.persona_card.speech_tics)}, "
                  f"观众称呼={self.persona_card.audience_nickname or '（无）'}")

        if pipeline == "harness_story_v1":
            self.state = None
            from ..harness.runtime import create_state
            return create_state(self, name, persona, background, topic, character_config, generation_seed, on_phase_callback)

        if pipeline == "legacy_v4":
            return self._create_legacy_v4_performance(
                name=name,
                persona=persona,
                background=background,
                topic=topic,
                language=language,
                character_config=character_config,
                on_phase_callback=on_phase_callback,
                generation_seed=generation_seed,
            )

        if on_phase_callback:
            on_phase_callback("Phase 0: 扩充人物小传 + 推导冲突...")
        ai_call_start = len(self.llm_gen.calls)
        stage_started = perf_counter()
        self.dossier = self._maybe_expand(
            name, persona, background, enable_dossier,
            tuning_guidance=self.tuning_guidance,
        )
        dossier_output = asdict(self.dossier) if self.dossier else None
        dossier_leaks = find_prompt_leaks(
            dossier_output, self.tuning_guidance,
        )
        self.debug_trace["stages"].append({
            "id": "dossier",
            "label": "人物扩充",
            "status": (
                "skipped" if not enable_dossier
                else "degraded" if dossier_leaks
                else "completed"
            ),
            "duration_ms": round((perf_counter() - stage_started) * 1000, 1),
            "ai": self.llm_gen.summarize_since(ai_call_start),
            "input": {
                "name": name,
                "persona": persona,
                "background": background,
                "persona_card": persona_card,
            },
            "metrics": {
                "enabled": enable_dossier,
                "cache_hit": self._dossier_cache_hit,
                "has_conflict": bool(self.dossier and self.dossier.conflict.statement),
                "prompt_version": DOSSIER_PROMPT_VERSION,
                "prompt_leak_detected": bool(dossier_leaks),
                "prompt_leak_markers": list(dossier_leaks),
            },
            "output": dossier_output,
        })
        if self.dossier and not self.dossier.is_empty():
            print(f"🎭 人物小传冲突: {self.dossier.conflict.statement}")

        if on_phase_callback:
            on_phase_callback("Phase A: 分析人设三轴 + 故事内核...")
        ai_call_start = len(self.llm_gen.calls)
        stage_started = perf_counter()
        rich_persona, story_core = self.persona_analyst.analyze(
            name=name, persona=persona, topic=topic, background=background, llm=self.llm_gen,
            card=self.persona_card, dossier=self.dossier,
            tuning_guidance=self.tuning_guidance,
            tone_intent=tone_intent,
            depth_budget=depth_budget,
            entertainment_mechanism=entertainment_mechanism,
        )
        analysis_output = {
            "rich_persona": asdict(rich_persona),
            "story_core": asdict(story_core),
        }
        analysis_leaks = find_prompt_leaks(
            analysis_output, self.tuning_guidance,
        )
        self.debug_trace["stages"].append({
            "id": "persona_analysis",
            "label": "人设、话语类型与内容内核",
            "status": "degraded" if analysis_leaks else "completed",
            "duration_ms": round((perf_counter() - stage_started) * 1000, 1),
            "ai": self.llm_gen.summarize_since(ai_call_start),
            "input": {
                "name": name,
                "persona": persona,
                "background": background,
                "topic": topic,
                "tone_intent": tone_intent,
                "depth_budget": depth_budget,
                "entertainment_mechanism": entertainment_mechanism,
                "dossier": asdict(self.dossier) if self.dossier else None,
            },
            "output": analysis_output,
            "metrics": {
                "discourse_mode": story_core.discourse_mode,
                "prompt_version": ANALYST_PROMPT_VERSION,
                "prompt_leak_detected": bool(analysis_leaks),
                "prompt_leak_markers": list(analysis_leaks),
            },
        })

        if on_phase_callback:
            on_phase_callback("Phase B: 排时间/音色骨架...")
        # 节目长度自适应：非叙事默认不超过 3 段，避免为了凑四段换框架、加支线。
        max_units = 4 if story_core.discourse_mode == "narrative" else 3
        n_units = max(2, min(max_units, len(story_core.story_beats) or 3))
        stage_started = perf_counter()
        units = self.unit_planner.plan(n_units)
        self.debug_trace["stages"].append({
            "id": "unit_plan",
            "label": "节目分段",
            "status": "completed",
            "duration_ms": round((perf_counter() - stage_started) * 1000, 1),
            "ai": {"uses_ai": False, "call_count": 0},
            "input": {
                "requested_unit_count": n_units,
                "story_beats": list(story_core.story_beats),
                "spine": story_core.spine,
                "supporting_points": list(story_core.supporting_points),
                "emotional_undercurrent": story_core.emotional_undercurrent,
            },
            "metrics": {"unit_count": len(units)},
            "output": [asdict(unit) for unit in units],
        })
        print(f"🎚️ 节目长度: {len(units)} 段 (~{int(len(units) * 75 / 60)}min，按故事自适应)")

        if on_phase_callback:
            on_phase_callback("Phase C: 抽相关 few-shot + 编剧创作...")
        examples = ""
        few_shot_ids: list[str] = []
        if self.example_sampler:
            lang = "en" if str(language).lower().startswith("en") else "zh"
            examples, few_shot_ids = self.example_sampler.get_relevant_examples_with_ids(
                topic=topic, persona=persona, n=2, language=lang,
                seed=retrieval_seed,
                allowed_ids=retrieval_allowed_ids,
                exclude_ids=retrieval_exclude_ids or (),
            )
        n_ex = len(few_shot_ids)
        print(f"📚 few-shot 示例注入: {n_ex} 条 (sampler={'on' if self.example_sampler else 'off'})")
        writer_input = {
            "rich_persona": asdict(rich_persona),
            "story_core": asdict(story_core),
            "unit_skeleton": [asdict(unit) for unit in units],
            "few_shot_count": n_ex,
            "few_shot_ids": few_shot_ids,
            "retrieval_seed": retrieval_seed,
            "dossier": asdict(self.dossier) if self.dossier else None,
            "tuning_guidance_ids": guidance_ids,
        }
        ai_call_start = len(self.llm_gen.calls)
        stage_started = perf_counter()
        units = self.script_writer.write(
            rich_persona, story_core, units, examples=examples, topic=topic,
            card=self.persona_card, dossier=self.dossier,
            source_material={
                "name": name,
                "persona": persona,
                "background": background,
                "topic": topic,
                "tone_intent": tone_intent,
                "depth_budget": depth_budget,
                "entertainment_mechanism": entertainment_mechanism,
            },
            tuning_guidance=self.tuning_guidance,
        )
        script_output = [asdict(unit) for unit in units]
        script_leaks = find_prompt_leaks(
            script_output, self.tuning_guidance,
        )
        self.debug_trace["stages"].append({
            "id": "script_writer",
            "label": "剧本生成",
            "status": (
                "degraded"
                if script_leaks or not self.script_writer.last_quality_gate["passed"]
                else "completed"
            ),
            "duration_ms": round((perf_counter() - stage_started) * 1000, 1),
            "ai": self.llm_gen.summarize_since(ai_call_start),
            "input": writer_input,
            "metrics": {
                "few_shot_count": n_ex,
                "few_shot_ids": few_shot_ids,
                "retrieval_seed": retrieval_seed,
                "unit_count": len(units),
                "line_count": sum(len(unit.lines) for unit in units),
                "spine": story_core.spine,
                "supporting_point_count": len(story_core.supporting_points),
                "quality_gate_retried": self.script_writer.last_quality_gate["retried"],
                "quality_gate_sanitized": self.script_writer.last_quality_gate["sanitized"],
                "quality_gate_passed": self.script_writer.last_quality_gate["passed"],
                "quality_gate_first_issues": self.script_writer.last_quality_gate["first_issues"],
                "quality_gate_retry_issues": self.script_writer.last_quality_gate["retry_issues"],
                "quality_gate_first_issue_details": self.script_writer.last_quality_gate[
                    "first_issue_details"
                ],
                "quality_gate_retry_issue_details": self.script_writer.last_quality_gate[
                    "retry_issue_details"
                ],
                "quality_gate_post_sanitize_issues": self.script_writer.last_quality_gate[
                    "post_sanitize_issues"
                ],
                "quality_gate_post_sanitize_issue_details": self.script_writer.last_quality_gate[
                    "post_sanitize_issue_details"
                ],
                "prompt_version": WRITER_PROMPT_VERSION,
                "prompt_leak_detected": bool(script_leaks),
                "prompt_leak_markers": list(script_leaks),
            },
            "output": script_output,
        })

        # call-back 埋梗账本：writer 埋的意象优先，兜底用生活化锚点（closing 回收用）
        from echuu.core.gag_ledger import GagLedger
        self.gag_ledger = GagLedger()
        self.gag_ledger.register(
            self.script_writer.last_planted_gags or list(rich_persona.life_anchors[:1]),
        )
        if len(self.gag_ledger):
            print(f"🪃 已埋梗: {self.gag_ledger.unrecalled()}")

        if on_phase_callback:
            on_phase_callback("Phase D: 清洗升华...")
        pre_process_line_count = sum(len(unit.lines) for unit in units)
        stage_started = perf_counter()
        for u in units:
            self.structure_breaker.delete_sublimation_in_unit(u)
        postprocess_output = [asdict(unit) for unit in units]
        postprocess_leaks = find_prompt_leaks(
            postprocess_output, self.tuning_guidance,
        )
        self.debug_trace["stages"].append({
            "id": "postprocess",
            "label": "后处理",
            "status": "degraded" if postprocess_leaks else "completed",
            "duration_ms": round((perf_counter() - stage_started) * 1000, 1),
            "ai": {"uses_ai": False, "call_count": 0},
            "input": {"line_count": pre_process_line_count},
            "metrics": {
                "line_count": sum(len(unit.lines) for unit in units),
                "planted_gags": list(self.script_writer.last_planted_gags),
                "prompt_leak_detected": bool(postprocess_leaks),
                "prompt_leak_markers": list(postprocess_leaks),
            },
            "output": postprocess_output,
        })

        show = Show(persona=rich_persona, topic=topic, units=units, story_core=story_core)

        # Flatten units → script_lines for V4-compat consumers (PerformerV3, _save_script)
        flat_lines = [line for u in show.units for line in u.lines]

        catchphrases = []
        if self.analyzer:
            catchphrases = [cp for cp, _ in self.analyzer.extract_catchphrases(language)[:5]]

        memory = PerformerMemory()
        memory.script_progress["total_lines"] = len(flat_lines)
        memory.script_progress["current_stage"] = flat_lines[0].stage if flat_lines else "hook"

        self.performer = PerformerV3(
            self.llm,
            self.tts,
            self.danmaku_handler,
            stream_lang_context=self.stream_lang_context,
        )

        self._save_script_v5(show, name, topic)
        self._print_script_preview_v5(show)

        return PerformanceState(
            name=name,
            persona=persona,
            background=background,
            topic=topic,
            show=show,
            script_lines=flat_lines,  # compat
            memory=memory,
            catchphrases=catchphrases,
        )

    @trace_runtime
    def run(
        self,
        max_steps: int = 12,
        danmaku_sim: Optional[List[Dict]] = None,
        play_audio: bool = False,
        save_audio: bool = False,
        convert_to_mp3: bool = True,
        segment_gap_ms: int = 0,
    ):
        """V5 实时表演（按 unit 渲染，per-unit acoustic switching）。"""
        if not self.state or not getattr(self.state, "show", None):
            raise RuntimeError("请先调用 setup() — Show 未生成")

        # Pre-populate danmaku queue (sim mode)
        if danmaku_sim:
            for dm in danmaku_sim:
                text = dm.get("text", "")
                user = dm.get("user", "观众")
                self.state.danmaku_queue.append(Danmaku.from_text(text, user=user))

        if save_audio and self.tts.enabled:
            self.tts.start_recording()

        print(f"\n{'='*60}")
        print("开始实时表演 (V5)")
        if save_audio and self.tts.enabled:
            print(f"正在录制... (输出格式: {'MP3' if convert_to_mp3 else 'WAV'})")
        print(f"{'='*60}\n")

        # 弹幕穿插的节奏控制：每讲完一行后可能插一条回应（冷却+每单元上限）
        lines_since_resp = 0
        resp_in_unit = 0
        last_unit_idx = -1
        last_line_text = ""
        # 互动窗口（Phase 2-①）：interact 行抛出问题后 N 行内冷却减半、上限翻倍；
        # 一行之后弹幕仍空 → 自嘲兜底一句（人设化生成，不写死）
        interact_window = 0
        interact_question = ""
        quip_armed = False

        for ev in self._render_show():
            unit_idx = ev["unit"]["index"]
            if unit_idx != last_unit_idx:
                resp_in_unit = 0          # 进新单元，回应额度重置
                last_unit_idx = unit_idx
            self.state.current_unit_idx = unit_idx
            self.state.current_line_in_unit = ev["line"]["index_in_unit"]
            # 切块后 max_steps 仍按"行"计：只在行首块 +1，行中间不 break
            is_first_chunk = ev["line"].get("chunk_index", 0) == 0
            is_last_chunk = ev["line"].get("chunk_last", True)
            if is_first_chunk:
                self.state.current_step += 1
            self.state.memory.script_progress["current_line"] = self.state.current_step
            self.state.memory.script_progress["current_stage"] = ev["line"]["stage"]
            last_line_text = ev["line"]["text"]

            print(f"[Unit {unit_idx+1}/{len(self.state.show.units)} · {ev['line']['stage']}] {last_line_text[:80]}{'...' if len(last_line_text) > 80 else ''}")
            if ev["line"]["is_rupture"] and is_first_chunk:
                print(f"  💥 rupture_kind={ev['unit']['rupture_kind']}")

            yield ev
            if not is_last_chunk:
                continue  # 行没播完不计弹幕冷却、不判停
            lines_since_resp += 1
            self.state.lines_since_tangent = getattr(self.state, "lines_since_tangent", 99) + 1
            self._maybe_release_tangent_card()

            # interact 行落地 → 开互动窗口
            if ev["line"]["stage"] == "interact":
                interact_window = 4
                interact_question = last_line_text
                quip_armed = True
                print(f"  🙋 互动点已抛出，窗口开启: {interact_question[:40]}")
            elif interact_window > 0:
                interact_window -= 1

            if self.state.current_step >= max_steps:
                break

            # 自然断点：穿插一条弹幕回应（常规：冷却 ≥2 行、每单元 ≤1 条；
            # 互动窗口内：冷却减半、每单元上限翻倍）
            in_window = interact_window > 0
            cooldown_ok = lines_since_resp >= (1 if in_window else 2)
            quota_ok = resp_in_unit < (2 if in_window else 1)
            has_tangent = any(getattr(d, "kind", "") == "tangent" for d in self.state.danmaku_queue)
            if self.state.danmaku_queue and (has_tangent or (cooldown_ok and quota_ok)):
                resp_ev = self._maybe_interleave_danmaku(unit_idx, last_line_text)
                if resp_ev is not None:
                    resp_in_unit += 1
                    lines_since_resp = 0
                    quip_armed = False  # 有人理了，不用自嘲
                    self.state.current_step += 1
                    print(f"  💬 回应弹幕[{resp_ev['danmaku']['user']}]: {resp_ev['speech'][:60]}")
                    yield resp_ev
                    if self.state.current_step >= max_steps:
                        break
            # 互动点抛出一行之后弹幕仍空 → 自嘲兜底（只一次）
            elif quip_armed and interact_window == 3 and not self.state.danmaku_queue:
                quip_armed = False
                quip_ev = self._maybe_no_reaction_quip(unit_idx, interact_question)
                if quip_ev is not None:
                    self.state.current_step += 1
                    print(f"  🫥 没人理，自嘲兜底: {quip_ev['speech'][:50]}")
                    yield quip_ev
                    if self.state.current_step >= max_steps:
                        break

        if save_audio and self.tts.enabled:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = ".mp3" if convert_to_mp3 else ".wav"
            audio_path = self.scripts_dir / f"{timestamp}_{self.state.name}_{self.state.topic[:20].replace(' ', '_')}_live{ext}"
            self.tts.save_recording(str(audio_path), convert_to_mp3=convert_to_mp3,
                                    keep_wav=False, gap_ms=segment_gap_ms)

        print(f"\n{'='*60}\n表演结束！\n{'='*60}\n")

    def _render_show(self):
        """Iterate through Show.units, switching acoustic per unit.

        Yields one step-event dict per line. TTS failures yield audio=None
        but do NOT abort the loop (spec §5)."""
        from dataclasses import asdict as _asdict
        from .tts_instruction import build_instruction, infer_cls
        from .breath import chunk_text
        show = self.state.show
        for unit in show.units:
            self.tts.update_session(**_asdict(unit.acoustic))
            for line_in_unit, line in enumerate(unit.lines):
                self.tts.set_instruction(build_instruction(unit, line))
                # 长台词按呼吸组切成短 step：字幕短、动作切换勤、step 间自带换气
                chunks = chunk_text(line.text) or [line.text]
                for ci, chunk in enumerate(chunks):
                    safe = sanitize_audience_text(
                        chunk,
                        source_material=getattr(self, "_source_material", None),
                        tuning_guidance=getattr(self, "tuning_guidance", None),
                    )
                    chunk = safe.text
                    try:
                        audio = self.tts.synthesize(chunk)
                    except Exception:
                        audio = None
                    ev = self._build_step_event(
                        unit, line_in_unit, line, audio,
                        text=chunk, chunk_index=ci, chunk_last=(ci == len(chunks) - 1),
                    )
                    ev["cls"] = infer_cls(unit, line)
                    yield ev

    def _build_step_event(self, unit, line_in_unit: int, line, audio,
                          text: str = None, chunk_index: int = 0, chunk_last: bool = True):
        """Construct WS-bound step event payload. v2 schema (spec §3.2).

        text/chunk_*：长台词切块时本 step 的实际文本与块位置；
        续块不重发动作 hint（保持 idle，前端文本语义匹配仍可触发手势）。
        pause_after：本行播完后的"思考停顿"秒数——interact 行等观众反应、
        unit 收尾行喘口气想下一段；前端在换气间隙上叠加，避免连珠炮输出。
        """
        from dataclasses import asdict as _asdict
        speech = text if text is not None else line.text
        pause_after = 0.0
        if chunk_last:
            if line.stage == "interact":
                pause_after = round(random.uniform(6.0, 10.0), 1)
            elif line_in_unit == len(unit.lines) - 1:
                pause_after = round(random.uniform(5.0, 8.0), 1)
        tokens = []
        if chunk_index == 0:
            from echuu.live.story_tokens import extract_tokens_from_line
            tokens = extract_tokens_from_line(
                speech,
                getattr(line, "key_info", None),
                getattr(line, "id", "") or f"u{unit.index}l{line_in_unit}",
            )
        return {
            "type": "step",
            "tokens": tokens,
            "show": {
                "total_units": len(self.state.show.units),
                "total_lines": sum(len(u.lines) for u in self.state.show.units),
            },
            "unit": {
                "index": unit.index,
                "axis": unit.axis,
                "density": unit.density,
                "time_window": list(unit.time_window),
                "acoustic": _asdict(unit.acoustic),
                "rupture_kind": next((r.kind for r in unit.rupture_slots), None) if line.is_rupture else None,
            },
            "line": {
                "index_in_unit": line_in_unit,
                "id": line.id,
                "stage": line.stage,
                "text": speech,
                "is_rupture": line.is_rupture,
                "chunk_index": chunk_index,
                "chunk_last": chunk_last,
                "cue": _asdict(line.cue) if hasattr(line.cue, "__dataclass_fields__") and line.cue else None,
            },
            "speech": speech,  # compat alias for audio-caption sync (spec §3.2)
            "pause_after": pause_after,
            "motion": pick_motion_for_step(unit, line, line_in_unit) if chunk_index == 0
                      else {"state": "idle", "loop": True},
            "audio": audio,
        }

    def emit_steering(self, dm, status: str, **extra):
        """广播一条观众触发的排队状态（queued → processing → applied → replied）。"""
        if dm is None:
            return None
        dm.status = status
        queue = getattr(self.state, "danmaku_queue", []) if self.state else []
        position = next((i + 1 for i, item in enumerate(queue) if getattr(item, "id", None) == dm.id), 0)
        payload = {
            "type": "steering",
            "item": {**dm.to_public(), **extra},
            "queue_size": len(queue),
            "queue_position": position,
        }
        callback = getattr(self, "on_steering", None)
        if callback:
            callback(payload)
        return payload

    def emit_token_hunt(self, payload: dict):
        """广播线索收集 / 跑毛点就绪。"""
        callback = getattr(self, "on_token_hunt", None)
        if callback:
            callback(payload)
        return payload

    def _ensure_token_hunt(self):
        from echuu.live.story_tokens import TokenHunt
        hunt = getattr(self.state, "token_hunt", None) if self.state else None
        if hunt is None and self.state is not None:
            hunt = TokenHunt()
            self.state.token_hunt = hunt
        return hunt

    def collect_story_token(self, token: dict | None) -> dict:
        hunt = self._ensure_token_hunt()
        if hunt is None:
            return {"slots": [], "pending": None, "crafted": None}
        result = hunt.collect(token)
        # 点满 3 个立刻合成。cooldown 只拦 tangent 之后自动补卡，不要让托盘停在 3/3 却点不了。
        crafted = hunt.maybe_craft(True)
        if crafted.get("crafted"):
            result = crafted
        else:
            result = {"slots": crafted["slots"], "pending": crafted["pending"], "crafted": None}
        self.emit_token_hunt({"type": "token_collect", "token": token, **result})
        if result.get("crafted"):
            self.emit_token_hunt({"type": "tangent_ready", "pending": result["pending"], "slots": result["slots"]})
        return result

    def consume_tangent_card(self) -> list | None:
        hunt = self._ensure_token_hunt()
        if hunt is None:
            return None
        return hunt.consume_pending()

    def _maybe_release_tangent_card(self):
        hunt = getattr(self.state, "token_hunt", None) if self.state else None
        if hunt is None or self.state is None:
            return
        if getattr(self.state, "lines_since_tangent", 0) < 2:
            return
        result = hunt.maybe_craft(True)
        if result.get("crafted"):
            self.emit_token_hunt({"type": "tangent_ready", "pending": result["pending"], "slots": result["slots"]})

    def _pick_danmaku(self):
        """从队列里挑一条最该回应的触发（跑毛卡 > 投喂/SC > 问题 > 其它），并移除它。"""
        q = self.state.danmaku_queue
        if not q:
            return None
        best_i, best_key = 0, (-1, -1, -1, -1)
        for i, d in enumerate(q):
            kind = getattr(d, "kind", "chat")
            key = (
                3 if kind == "tangent" else 0,
                2 if kind == "gift" or getattr(d, "is_sc", False) else 0,
                1 if d.is_question() else 0,
                int(getattr(d, "amount", 0) or 0),
            )
            if key > best_key:
                best_key, best_i = key, i
        picked = q.pop(best_i)
        self.emit_steering(picked, "processing")
        return picked

    def _upcoming_script_lines(self):
        """还没播出的台词（当前行之后）。"""
        show = getattr(self.state, "show", None)
        if not show or not getattr(show, "units", None):
            return []
        unit_idx = getattr(self.state, "current_unit_idx", 0)
        line_idx = getattr(self.state, "current_line_in_unit", 0)
        upcoming = []
        for index, unit in enumerate(show.units):
            if index < unit_idx:
                continue
            start = line_idx + 1 if index == unit_idx else 0
            for offset, line in enumerate(unit.lines[start:]):
                upcoming.append((unit, start + offset, line))
        return upcoming

    @guard_steering
    def _steer_remaining_show(self, dm) -> str:
        """改还没讲的走向：挂 beats + 重写下一句。失败不挡当场回应。"""
        show = getattr(self.state, "show", None)
        if show is None:
            return "已记下"
        trigger = dm.text
        kind = getattr(dm, "kind", "chat")
        if kind == "gift":
            trigger = trigger or f"投喂了 {getattr(dm, 'gift_id', '') or '礼物'}"
        if kind == "tangent":
            from echuu.live.story_tokens import compose_tangent_text
            trigger = compose_tangent_text(getattr(dm, "entities", None) or [])
        if getattr(show, "story_core", None) is not None:
            show.story_core = annotate_remaining_beats(show.story_core, trigger)
        upcoming = self._upcoming_script_lines()
        if not upcoming:
            return "已记下，后面会接住"
        core = getattr(show, "story_core", None)
        spine = getattr(core, "spine", "") if core else ""
        if kind == "tangent":
            notes = []
            branch = self.story_steerer.rewrite_line(
                spine=spine,
                topic=self.state.topic,
                user=dm.user,
                trigger=trigger,
                kind=kind,
                line=getattr(upcoming[0][2], "text", "") or "",
                entities=getattr(dm, "entities", None) or [],
                role="branch",
            )
            if branch:
                upcoming[0][2].text = branch
                notes.append(branch[:32])
            if len(upcoming) > 1:
                back = self.story_steerer.rewrite_line(
                    spine=spine,
                    topic=self.state.topic,
                    user=dm.user,
                    trigger=trigger,
                    kind=kind,
                    line=getattr(upcoming[1][2], "text", "") or "",
                    entities=getattr(dm, "entities", None) or [],
                    role="return",
                )
                if back:
                    upcoming[1][2].text = back
                    notes.append("回主线")
            return " · ".join(notes) if notes else "沿着线索讲一小段再回来"
        _, _, line = upcoming[0]
        rewritten = self.story_steerer.rewrite_line(
            spine=spine,
            topic=self.state.topic,
            user=dm.user,
            trigger=trigger,
            kind=kind,
            line=getattr(line, "text", "") or "",
        )
        if rewritten:
            line.text = rewritten
            return rewritten[:48]
        return "后续会往观众推的方向收"

    def _maybe_interleave_danmaku(self, unit_idx: int, last_line: str):
        """生成一条人设化弹幕回应并构造 step 事件；失败返回 None（跳过穿插）。"""
        dm = self._pick_danmaku()
        if dm is None:
            return None
        note = self._steer_remaining_show(dm)
        if getattr(dm, "kind", "chat") == "tangent":
            self.state.lines_since_tangent = 0
            self.emit_steering(dm, "applied", note=note)
            self.emit_steering(dm, "replied")
            return None
        self.emit_steering(dm, "applied", note=note)
        persona = getattr(self.state.show, "persona", None)
        identity = getattr(persona, "identity", "") if persona else ""
        verbal_tics = getattr(persona, "verbal_tics", ()) if persona else ()
        reply = self.danmaku_interleaver.respond(
            identity=identity, verbal_tics=verbal_tics, topic=self.state.topic,
            last_line=last_line, danmaku_text=dm.text, user=dm.user,
            card=getattr(self, "persona_card", None),
            gags=self.gag_ledger.unrecalled() if getattr(self, "gag_ledger", None) else None,
        )
        if not reply:
            user = (getattr(dm, "user", "") or "你").strip() or "你"
            snippet = (dm.text or "").strip()[:10]
            reply = f"{user}我看见了{'，' + snippet if snippet else ''}，我接着讲。"
        reply = sanitize_audience_text(
            reply,
            source_material=getattr(self, "_source_material", None),
            tuning_guidance=getattr(self, "tuning_guidance", None),
        ).text
        if not approve_reply(self, reply):
            return None
        try:
            audio = self.tts.synthesize(reply) if getattr(self, "tts", None) else None
        except Exception:
            audio = None
        unit = self.state.show.units[unit_idx]
        event = self._build_danmaku_event(unit, dm, reply, audio)
        self.emit_steering(dm, "replied")
        return event

    def _maybe_no_reaction_quip(self, unit_idx: int, question: str):
        """互动点没人理时的自嘲兜底 step（人设化生成，失败返回 None 直接跳过）。"""
        persona = getattr(self.state.show, "persona", None)
        quip = self.danmaku_interleaver.no_reaction_quip(
            identity=getattr(persona, "identity", "") if persona else "",
            verbal_tics=getattr(persona, "verbal_tics", ()) if persona else (),
            topic=self.state.topic,
            question=question,
        )
        if not quip:
            return None
        quip = sanitize_audience_text(
            quip,
            source_material=getattr(self, "_source_material", None),
            tuning_guidance=getattr(self, "tuning_guidance", None),
        ).text
        if not approve_reply(self, quip):
            return None
        try:
            audio = self.tts.synthesize(quip) if getattr(self, "tts", None) else None
        except Exception:
            audio = None
        unit = self.state.show.units[unit_idx]
        ev = self._build_danmaku_event(unit, None, quip, audio)
        ev.pop("danmaku", None)
        ev["is_danmaku_response"] = False
        ev["line"]["stage"] = "interact_quip"
        return ev

    def _build_danmaku_event(self, unit, danmaku, reply: str, audio):
        """弹幕回应的 step 事件（v2 形状 + danmaku 字段，line.stage='danmaku'）。"""
        from dataclasses import asdict as _asdict
        return {
            "type": "step",
            "show": {
                "total_units": len(self.state.show.units),
                "total_lines": sum(len(u.lines) for u in self.state.show.units),
            },
            "unit": {
                "index": unit.index,
                "axis": unit.axis,
                "density": unit.density,
                "time_window": list(unit.time_window),
                "acoustic": _asdict(unit.acoustic),
                "rupture_kind": None,
            },
            "line": {
                "index_in_unit": -1,
                "stage": "danmaku",
                "text": reply,
                "is_rupture": False,
                "cue": None,
            },
            "speech": reply,
            "danmaku": {
                "id": getattr(danmaku, "id", ""),
                "text": danmaku.text,
                "user": danmaku.user,
                "kind": getattr(danmaku, "kind", "chat"),
            } if danmaku else None,
            "is_danmaku_response": danmaku is not None,
            "motion": pick_motion_for_danmaku(unit, danmaku) if danmaku else {"state": "speaking"},
            "audio": audio,
        }

    def run_streaming(
        self,
        max_steps: int = 12,
        danmaku_sim: Optional[List[Dict]] = None,
        save_audio: bool = True,
        convert_to_mp3: bool = True,
    ):
        """
        实时流式直播模式 - 串行播放音频，段落间有自然停顿（1-5秒）

        Args:
            max_steps: 最大步数
            danmaku_sim: 弹幕模拟数据
            save_audio: 是否保存音频
            convert_to_mp3: 是否转换为 MP3（默认 True）
        """
        if not self.state:
            raise RuntimeError("请先调用 setup() 或 create_performance()")

        # 使用 StreamSimulator 进行流式播放
        simulator = StreamSimulator()

        # 设置录制
        if save_audio and self.tts.enabled:
            self.tts.start_recording()

        # 获取生成器
        generator = self.run(
            max_steps=max_steps,
            danmaku_sim=danmaku_sim,
            play_audio=False,  # 不在 run 中播放
            save_audio=False,  # 不在 run 中保存
        )

        # 模拟流式直播
        total_duration = simulator.simulate_live_stream(
            generator,
            show_progress=True,
            show_memory=True
        )

        # 保存音频
        if save_audio and self.tts.enabled:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = ".mp3" if convert_to_mp3 else ".wav"
            audio_path = self.scripts_dir / f"{timestamp}_{self.state.name}_{self.state.topic[:20].replace(' ', '_')}_live{ext}"
            self.tts.save_recording(str(audio_path), convert_to_mp3=convert_to_mp3, keep_wav=False)

    def _save_script_v5(self, show, name: str, topic: str):
        """V5 Show → JSON dump."""
        from dataclasses import asdict
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_topic = topic[:30].replace(" ", "_").replace("/", "_")
        filename = f"{timestamp}_{name}_{safe_topic}.json"
        filepath = self.scripts_dir / filename

        script_data = {
            "metadata": {
                "timestamp": timestamp,
                "name": name,
                "topic": topic,
                "total_units": len(show.units),
                "total_lines": sum(len(u.lines) for u in show.units),
            },
            "persona": asdict(show.persona),
            "units": [
                {
                    "index": u.index,
                    "time_window": list(u.time_window),
                    "axis": u.axis,
                    "density": u.density,
                    "acoustic": asdict(u.acoustic),
                    "rupture_slots": [asdict(r) for r in u.rupture_slots],
                    "lines": [
                        {
                            "id": l.id,
                            "text": l.text,
                            "stage": l.stage,
                            "is_rupture": l.is_rupture,
                            "interruption_cost": l.interruption_cost,
                        }
                        for l in u.lines
                    ],
                }
                for u in show.units
            ],
        }

        with filepath.open("w", encoding="utf-8") as f:
            json.dump(script_data, f, ensure_ascii=False, indent=2)
        print(f"V5 剧本已保存: {filepath}")

    def _print_script_preview_v5(self, show):
        """V5 Show → console preview."""
        print("\n生成的剧本（V5）：")
        print("=" * 60)
        for u in show.units:
            print(f"\n--- Unit {u.index + 1} / 4 · axis={u.axis} · density={u.density} ---")
            print(f"  acoustic: pitch={u.acoustic.pitch_rate}, rate={u.acoustic.speech_rate}, vol={u.acoustic.volume}")
            for i, line in enumerate(u.lines):
                rupture_marker = " 💥" if line.is_rupture else ""
                preview = line.text[:80] + ("..." if len(line.text) > 80 else "")
                print(f"  [{i}] {line.stage}{rupture_marker}  {preview}")
        print("\n" + "=" * 60 + "\n")
