import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class TTSConfig(BaseModel):
    """单个语音实例的配置；直接构造时不读取环境变量。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    api_key: SecretStr | None = None
    model: str = "qwen3-tts-flash-realtime"
    voice: str = Field(default="Cherry", min_length=1)
    mode: str = "server_commit"
    language_type: str = "Chinese"
    response_format: str = "pcm"
    sample_rate: int = Field(default=24000, gt=0)
    speech_rate: float = Field(default=1.0, gt=0)
    speech_rate_cap: float = Field(default=1.0, gt=0)
    pitch_rate: float = Field(default=1.0, gt=0)
    volume: int = Field(default=50, ge=0, le=100)
    bit_rate: int = Field(default=128, gt=0)
    timeout: float = Field(default=12.0, gt=0)
    ws_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"

    @classmethod
    def from_env(cls) -> "TTSConfig":
        """一次性读取已存在的环境变量，不加载 .env 文件。"""
        values: dict[str, object] = {
            name: os.environ[f"TTS_{name.upper()}"]
            for name in cls.model_fields
            if f"TTS_{name.upper()}" in os.environ
        }
        values["api_key"] = os.getenv("DASHSCOPE_API_KEY")
        if "ws_url" not in values and os.getenv("TTS_REGION", "cn").lower() in {
            "sg",
            "intl",
            "international",
        }:
            values["ws_url"] = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
        return cls.model_validate(values)


class EngineConfig(BaseModel):
    """可嵌入引擎配置；未指定数据或输出目录时不读取语料、不自动导出。"""

    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str | None = None
    model: str | None = None
    api_key: SecretStr | None = None
    annotated_data_path: Path | None = None
    examples_path: Path | None = None
    output_dir: Path | None = None
    story_pipeline: Literal["legacy_v4", "refactor", "harness_story_v1"] = "legacy_v4"
    clip_model: str = "qwen3-max"
    gift_model_timeout_seconds: float = Field(
        default=2, gt=0, le=8, allow_inf_nan=False
    )
    tts: TTSConfig = Field(default_factory=TTSConfig)

    @classmethod
    def from_env(
        cls, *, project_root: Path | None = None, provider: str | None = None
    ) -> "EngineConfig":
        """构建兼容配置；仅显式提供仓库根目录时复用原数据和导出位置。"""
        provider = (
            provider or os.getenv("ECHUU_LLM_PROVIDER") or os.getenv("LLM_PROVIDER")
        )
        candidates = (
            ("qwen", "DASHSCOPE_API_KEY", "QWEN_MODEL", "qwen-plus"),
            ("gemini", "GEMINI_API_KEY", "GEMINI_MODEL", "gemini-3-flash-preview"),
            ("claude", "ANTHROPIC_API_KEY", "DEFAULT_MODEL", "claude-3-haiku-20240307"),
        )
        aliases = {
            "dashscope": "qwen",
            "alibaba": "qwen",
            "google": "gemini",
            "anthropic": "claude",
        }
        provider = (
            aliases.get(provider.strip().lower(), provider.strip().lower())
            if provider
            else None
        )
        selected = (
            next((item for item in candidates if item[0] == provider), None)
            if provider
            else next((item for item in candidates if os.getenv(item[1])), None)
        )
        values: dict[str, object] = {
            "provider": provider,
            "tts": TTSConfig.from_env(),
            "story_pipeline": os.getenv("ECHUU_STORY_PIPELINE", "legacy_v4"),
            "clip_model": os.getenv("ECHUU_CLIP_MODEL", "qwen3-max"),
        }
        if selected:
            name, key, model, default = selected
            values.update(
                provider=name, api_key=os.getenv(key), model=os.getenv(model, default)
            )
        if project_root is not None:
            data = project_root / "data"
            for field, name in (
                ("annotated_data_path", "annotated_clips.json"),
                (
                    "examples_path",
                    "vtuber_raw_clips_for_notebook_full_30_cleaned.jsonl",
                ),
            ):
                if (data / name).is_file():
                    values[field] = data / name
            values["output_dir"] = project_root / "output" / "scripts"
        return cls.model_validate(values)
