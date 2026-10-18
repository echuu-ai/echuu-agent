"""
TTS 客户端封装（基于 Qwen TTS Realtime API）。
使用随包发布的 Qwen TTS 实现。
支持自动转换为 MP3 格式。
"""

from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from typing import Optional

from echuu.diagnostics import diagnostic

from ..config import TTSConfig


def convert_wav_to_mp3(wav_path: str, mp3_path: str = None, bitrate: str = "128k") -> Optional[str]:
    """
    将 WAV 文件转换为 MP3 格式（使用 pydub + ffmpeg）

    Args:
        wav_path: WAV 文件路径
        mp3_path: MP3 输出路径（可选，默认与 wav 相同但扩展名改为 .mp3）
        bitrate: MP3 比特率（默认 128k）

    Returns:
        MP3 文件路径，如果转换失败则返回 None
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        diagnostic("⚠️ pydub 未安装，无法转换为 MP3")
        return None

    wav_file = Path(wav_path)
    if not wav_file.exists():
        diagnostic(f"⚠️ WAV 文件不存在: {wav_path}")
        return None

    if mp3_path is None:
        mp3_path = wav_file.with_suffix('.mp3')
    else:
        mp3_path = Path(mp3_path)

    try:
        diagnostic(f"  🔄 正在转换为 MP3...")
        audio = AudioSegment.from_wav(str(wav_file))
        audio.export(str(mp3_path), format="mp3", bitrate=bitrate)

        # 显示文件大小对比
        wav_size = wav_file.stat().st_size / (1024 * 1024)
        mp3_size = mp3_path.stat().st_size / (1024 * 1024)
        compression_ratio = (1 - mp3_size / wav_size) * 100

        diagnostic(f"  ✅ MP3 已保存: {mp3_path}")
        diagnostic(f"  📊 文件大小: {mp3_size:.1f}MB (WAV: {wav_size:.1f}MB, 压缩率: {compression_ratio:.0f}%)")

        return str(mp3_path)

    except Exception as exc:
        diagnostic(f"  ⚠️ MP3 转换失败: {exc}")
        return None



class TTSClient:
    """轻量 TTS 包装器，提供统一接口。"""

    def __init__(self, *, config: TTSConfig | None = None):
        explicit = config is not None
        self.config = config if explicit else TTSConfig.from_env()
        self.enabled = False
        self._recording = False
        self._recording_buffer = []
        self.tts = None
        self._prepared = {}
        self._prepare_lock = threading.Lock()
        self._prepare_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="speech-prepare")

        api_key = self.config.api_key.get_secret_value() if self.config.api_key else None
        if not api_key:
            diagnostic("未设置 DASHSCOPE_API_KEY，TTS 已禁用")
            return

        try:
            cosyvoice_cls = self._load_cosyvoice_class()
            if not cosyvoice_cls:
                raise ImportError("无法加载 CosyVoiceTTS")

            # Use qwen3-tts-flash-realtime model for multilingual support
            model = self.config.model
            voice = self.config.voice

            self.tts = cosyvoice_cls(
                api_key=api_key,
                model=model,
                voice=voice,
                config=self.config,
            )
            self.enabled = True
            diagnostic(f"✅ TTS 已启用: model={model}, voice={voice}")
        except Exception as exc:
            self.close_preparation()
            if explicit:
                raise
            diagnostic(f"TTS 初始化失败: {exc}")
            diagnostic("TTS disabled after initialization failure")

    @staticmethod
    def _load_cosyvoice_class():
        """返回包内实现，保留旧加载方法名称供现有调用者使用。"""
        from .qwen_tts import CosyVoiceTTS

        return CosyVoiceTTS

    def update_session(self, *, pitch_rate: float = 1.0, speech_rate: float = 1.0, volume: int = 50) -> None:
        """Pass segment-level acoustic settings to the underlying CosyVoiceTTS.

        Qwen3-TTS-Flash-Realtime only — silently no-op on non-realtime models.
        Clipping to legal ranges is defensive (spec §5)."""
        if not self.enabled or not self.tts:
            return
        # 直播听感限速：引擎的 HYPER_FLUENT 会推 1.1x，叠加长句会盖掉呼吸停顿
        rate_cap = getattr(getattr(self, "config", None), "speech_rate_cap", 1.0)
        pitch_rate  = max(0.7, min(1.3, pitch_rate))
        speech_rate = max(0.7, min(1.3, min(speech_rate, rate_cap)))
        volume      = max(0,   min(100, volume))
        try:
            self.tts.pitch_rate = pitch_rate
            self.tts.speech_rate = speech_rate
            self.tts.volume = volume
        except Exception as exc:
            diagnostic(f"[TTS] update_session failed: {exc}")

    def set_instruction(self, instruction: str) -> None:
        """设置下一次合成的自然语言情绪指令（仅 instruct 系列模型生效）。"""
        if not self.enabled or not self.tts:
            return
        try:
            self.tts.instruction = instruction or ""
        except Exception as exc:
            diagnostic(f"[TTS] set_instruction failed: {exc}")

    def fork_preparation(self):
        """Independent voice settings and worker pool; never touch the live recorder."""
        clone = copy.copy(self)
        clone.tts = copy.copy(self.tts)
        clone._recording = False
        clone._recording_buffer = []
        clone._prepared = {}
        clone._prepare_lock = threading.Lock()
        clone._prepare_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="interaction-speech")
        return clone

    def prepare(self, text: str):
        """Cache exact text + frozen voice settings; rewritten text is a cache miss."""
        if not self.enabled or not self.tts:
            raise RuntimeError("TTS is unavailable")
        voice = copy.copy(self.tts)
        fields = ("model", "voice", "language_type", "instruction", "speech_rate",
                  "pitch_rate", "volume", "sample_rate", "response_format", "mode", "bit_rate", "ws_url")
        key = (text, *(getattr(voice, field, None) for field in fields))
        with self._prepare_lock:
            future = self._prepared.get(key)
            if future is None:
                timing = {"queued_at": monotonic(), "attempts": 0, "status": "queued"}
                future = self._prepare_pool.submit(self._generate_prepared, voice, text, timing)
                future.preparation_timing = timing
                self._prepared[key] = future
        return future

    @staticmethod
    def _generate_prepared(voice, text, timing=None):
        from .breath import synthesize_with_breath
        started = monotonic()
        if timing is not None:
            timing.update(queue_seconds=round(started - timing["queued_at"], 3), status="running")
        try:
            for attempt in range(2):
                if timing is not None:
                    timing["attempts"] = attempt + 1
                try:
                    audio = synthesize_with_breath(voice.synthesize, text, sample_rate=voice.sample_rate, strict=True)
                    if audio:
                        if timing is not None:
                            timing["status"] = "ready"
                        return audio
                    raise RuntimeError("TTS returned no audio")
                except Exception:
                    if attempt:
                        raise
            raise RuntimeError("TTS failed")
        finally:
            if timing is not None:
                timing["synthesis_seconds"] = round(monotonic() - started, 3)
                if timing["status"] != "ready":
                    timing["status"] = "failed"

    def close_preparation(self):
        self._prepare_pool.shutdown(wait=False, cancel_futures=True)

    def synthesize(self, text: str, emotion_boost: float = 0.0) -> Optional[bytes]:
        """
        合成语音（带规则化喘气停顿）。

        长句按句末标点切呼吸组分段合成，PCM 层拼接静音；
        （…）舞台指示在合成前剥离，不会被朗读。见 echuu/live/breath.py。

        Args:
            text: 合成文本
            emotion_boost: 情绪增强参数（保留接口，不影响当前实现）
        """
        if not self.enabled or not self.tts:
            return None

        from .breath import synthesize_with_breath

        sample_rate = getattr(self.tts, "sample_rate", 24000)
        try:
            audio = self.prepare(text).result()
        except Exception as exc:
            diagnostic(f"[TTS] 合成错误: {exc}")
            return None

        if self._recording and audio:
            self._recording_buffer.append(audio)

        return audio

    def start_recording(self):
        """开始录制音频片段。"""
        self._recording = True
        self._recording_buffer = []

    def save_recording(self, path: str, convert_to_mp3: bool = True, keep_wav: bool = False,
                       gap_ms: int = 0):
        """
        保存录制音频（支持自动转换为 MP3）

        Args:
            path: 保存路径
            convert_to_mp3: 是否转换为 MP3 格式（默认 True）
            keep_wav: 转换后是否保留 WAV 文件（默认 False）
            gap_ms: 段与段之间插入的静音（毫秒），让合并后的音频有喘气停顿（默认 0）
        """
        if not self._recording_buffer:
            diagnostic("没有可保存的录制音频")
            return

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from pydub import AudioSegment

            # 使用 pydub 合并所有音频片段
            diagnostic(f"  正在合并 {len(self._recording_buffer)} 个音频片段...")

            combined = AudioSegment.empty()
            gap = AudioSegment.silent(duration=gap_ms) if gap_ms > 0 else None
            for i, chunk in enumerate(self._recording_buffer):
                # 从 WAV 数据加载音频
                import io
                audio = AudioSegment.from_wav(io.BytesIO(chunk))
                if gap is not None and i > 0:
                    combined += gap  # 段间插静音，制造喘气停顿
                combined += audio
                if (i + 1) % 5 == 0:
                    diagnostic(f"    已合并 {i+1}/{len(self._recording_buffer)}...")

            # 导出为 MP3
            if convert_to_mp3:
                mp3_path = str(output_path).replace('.wav', '.mp3') if str(output_path).endswith('.wav') else str(output_path)
                if not mp3_path.endswith('.mp3'):
                    mp3_path += '.mp3'

                diagnostic(f"  🔄 正在导出 MP3...")
                combined.export(mp3_path, format="mp3", bitrate="128k")

                # 显示文件大小
                mp3_size = Path(mp3_path).stat().st_size / (1024 * 1024)
                total_wav_size = sum(len(chunk) for chunk in self._recording_buffer) / (1024 * 1024)
                compression_ratio = (1 - mp3_size / total_wav_size) * 100 if total_wav_size > 0 else 0

                diagnostic(f"  ✅ MP3 已保存: {Path(mp3_path).name}")
                diagnostic(f"  📊 文件大小: {mp3_size:.1f}MB (原始: {total_wav_size:.1f}MB, 压缩率: {compression_ratio:.0f}%)")
            else:
                # 导出为 WAV
                diagnostic(f"  🔄 正在导出 WAV...")
                combined.export(str(output_path), format="wav")
                wav_size = output_path.stat().st_size / (1024 * 1024)
                diagnostic(f"  ✅ WAV 已保存: {output_path.name} ({wav_size:.1f} MB)")

        except ImportError:
            # 如果 pydub 不可用，使用简单拼接（仅适用于 PCM）
            diagnostic("  ⚠️ pydub 不可用，使用简单拼接...")
            with output_path.open("wb") as f:
                for chunk in self._recording_buffer:
                    f.write(chunk)
            wav_size = sum(len(chunk) for chunk in self._recording_buffer)
            diagnostic(f"  ✅ 音频已保存: {output_path.name} ({wav_size / (1024*1024):.1f} MB)")

            # 如果需要转换但 pydub 不可用
            if convert_to_mp3:
                convert_wav_to_mp3(str(output_path))

        except Exception as exc:
            diagnostic(f"[TTS] 保存录制失败: {exc}")
            diagnostic("TTS disabled after initialization failure")

        finally:
            self._recording = False
