"""
Echuu - AI VTuber Auto-Live System

A library for generating natural, spontaneous-feeling live broadcast content
by learning from real streamer patterns.

Example usage:
    from echuu import EchuuLiveEngine

    engine = EchuuLiveEngine()
    engine.setup(
        name="六螺",
        persona="爱吐槽的女主播",
        topic="关于上司的八卦",
        background="前上班族，现全职主播"
    )

    for step in engine.run():
        print(step)
"""

try:
    from ._version import __version__
except ImportError:
    __version__ = "0+unbuilt"

from .config import EngineConfig, TTSConfig
from .core.digression_db import DigressionDB
from .core.drama_amplifier import DramaAmplifier
from .core.emotion_mixer import EmotionConfig, EmotionMixer
from .core.pattern_analyzer import PatternAnalyzer

# PerformerCue - Expression and gesture annotation
from .core.performer_cue import (
    BlinkCue,
    BlinkMode,
    CameraCue,
    EmotionCue,
    EmotionKey,
    GestureCue,
    LipsyncCue,
    LookCue,
    LookTarget,
    PerformerCue,
    infer_emotion_from_text,
)
from .core.persona_analyst import PersonaAnalyst

# V5 core
from .core.persona_model import PersonaModel, RichPersona
from .core.story_core import StoryCore

# Core components for customization
from .core.story_nucleus import StoryNucleus
from .core.structure_breaker import StructureBreaker
from .core.trigger_bank import TriggerBank, TriggerTemplate
from .core.unit import AcousticHint, Rupture, ScriptLine, Show, Unit
from .core.unit_planner import UnitPlanner
from .generators.example_sampler import ExampleSampler

# Generators
from .generators.script_writer import ScriptWriter
from .live.danmaku import DanmakuEvaluator, DanmakuHandler

# Main engine - the primary entry point
from .live.engine import EchuuLiveEngine
from .live.gemini_client import GeminiClient
from .live.llm_client import LLMClient
from .live.llm_factory import create_llm_client
from .live.performer import PerformerV3
from .live.response_generator import DanmakuResponseGenerator

# Live performance components
from .live.state import Danmaku, PerformanceState, PerformerMemory
from .live.tts_client import TTSClient

# VRM mapping
from .vrm.mapper import VRMExpressionMapper, VRMVersion
from .vrm.presets import GESTURE_PRESETS, GestureCategory, get_gesture_by_emotion

__all__ = [
    # Version
    "__version__",
    "EngineConfig",
    "TTSConfig",
    # Main engine
    "EchuuLiveEngine",
    # Core
    "StoryNucleus",
    "EmotionMixer",
    "EmotionConfig",
    "TriggerBank",
    "TriggerTemplate",
    "DigressionDB",
    "StructureBreaker",
    "PatternAnalyzer",
    "DramaAmplifier",
    # PerformerCue
    "PerformerCue",
    "EmotionCue",
    "GestureCue",
    "LookCue",
    "BlinkCue",
    "LipsyncCue",
    "CameraCue",
    "EmotionKey",
    "LookTarget",
    "BlinkMode",
    "infer_emotion_from_text",
    # VRM
    "VRMExpressionMapper",
    "VRMVersion",
    "GESTURE_PRESETS",
    "GestureCategory",
    "get_gesture_by_emotion",
    # Generators
    "ScriptWriter",
    "ExampleSampler",
    # V5/V2 core
    "PersonaModel",
    "RichPersona",
    "PersonaAnalyst",
    "StoryCore",
    "Show",
    "Unit",
    "Rupture",
    "ScriptLine",
    "AcousticHint",
    "UnitPlanner",
    # Live
    "Danmaku",
    "PerformerMemory",
    "PerformanceState",
    "PerformerV3",
    "LLMClient",
    "GeminiClient",
    "create_llm_client",
    "TTSClient",
    "DanmakuHandler",
    "DanmakuEvaluator",
    "DanmakuResponseGenerator",
]

from .session import LiveSession
from .session_types import (
    AudioDescriptor,
    Interaction,
    PerformanceStep,
    SessionEvent,
    SessionInput,
    SessionOptions,
)

__all__ += ["AudioDescriptor", "Interaction", "LiveSession", "PerformanceStep", "SessionEvent", "SessionInput", "SessionOptions"]

from .session_extensions import GiftBatchInput, GiftItem, SessionArtifacts

__all__ += ["GiftBatchInput", "GiftItem", "SessionArtifacts"]
