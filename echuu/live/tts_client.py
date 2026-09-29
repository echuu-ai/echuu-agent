"""
TTS 客户端封装（基于 Qwen TTS Realtime API）。
直接使用 workflow/backend/tts_client.py 的 CosyVoiceTTS 实现。
支持自动转换为 MP3 格式。
"""

from __future__ import annotations

import importlib.util
import os
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional


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
        print("⚠️ pydub 未安装，无法转换为 MP3")
        return None

    wav_file = Path(wav_path)
    if not wav_file.exists():
        print(f"⚠️ WAV 文件不存在: {wav_path}")
        return None

    if mp3_path is None:
        mp3_path = wav_file.with_suffix('.mp3')
    else:
        mp3_path = Path(mp3_path)

    try:
        print(f"  🔄 正在转换为 MP3...")
        audio = AudioSegment.from_wav(str(wav_file))
        audio.export(str(mp3_path), format="mp3", bitrate=bitrate)

        # 显示文件大小对比
        wav_size = wav_file.stat().st_size / (1024 * 1024)
        mp3_size = mp3_path.stat().st_size / (1024 * 1024)
        compression_ratio = (1 - mp3_size / wav_size) * 100

        print(f"  ✅ MP3 已保存: {mp3_path}")
        print(f"  📊 文件大小: {mp3_size:.1f}MB (WAV: {wav_size:.1f}MB, 压缩率: {compression_ratio:.0f}%)")

        return str(mp3_path)

    except Exception as exc:
        print(f"  ⚠️ MP3 转换失败: {exc}")
        return None


class TTSClient:
    """轻量 TTS 包装器，提供统一接口。"""

    def __init__(self):
        self.enabled = False
        self._recording = False
        self._recording_buffer = []
        self.tts = None
        self._prepared = {}
        self._prepare_lock = threading.Lock()
        self._prepare_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="speech-prepare")

        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            print("未设置 DASHSCOPE_API_KEY，TTS 已禁用")
            return

        try:
            cosyvoice_cls = self._load_cosyvoice_class()
            if not cosyvoice_cls:
                raise ImportError("无法加载 CosyVoiceTTS")

            # Use qwen3-tts-flash-realtime model for multilingual support
            model = os.getenv("TTS_MODEL", "qwen3-tts-flash-realtime")
            voice = os.getenv("TTS_VOICE", "Cherry")

            self.tts = cosyvoice_cls(
                api_key=api_key,
                model=model,
                voice=voice,
            )
            self.enabled = True
            print(f"✅ TTS 已启用: model={model}, voice={voice}")
        except Exception as exc:
            print(f"TTS 初始化失败: {exc}")
            import traceback
            traceback.print_exc()

    @staticmethod
    def _find_project_root() -> Path:
        """寻找项目根目录（包含 workflow 目录）。"""
        current = Path(__file__).resolve()
        # Go up directories to find the root
        for parent in [current] + list(current.parents):
            # Check for workflow directory (indicating echuu-agent root)
            if (parent / "workflow").exists() and (parent / "workflow" / "backend").exists():
                return parent
            # Also check for .git or requirements.txt as fallback
            if (parent / ".git").exists() or (parent / "requirements.txt").exists():
                return parent
        # Fallback to current working directory
        return Path.cwd()

    def _load_cosyvoice_class(self):
        """
        动态加载 workflow/backend/tts_client.py 中的 CosyVoiceTTS。
        """
        project_root = self._find_project_root()
        module_path = project_root / "workflow" / "backend" / "tts_client.py"

        if not module_path.exists():
            # Try going up one more level (in case we're in echuu-sdk-release)
            alt_root = project_root.parent
            module_path = alt_root / "workflow" / "backend" / "tts_client.py"

        if not module_path.exists():
            print(f"⚠️ 找不到 TTS 模块: {module_path}")
            print(f"   当前工作目录: {Path.cwd()}")
            print(f"   项目根目录: {project_root}")
            return None

        spec = importlib.util.spec_from_file_location("workflow_backend_tts_client", module_path)
        if not spec or not spec.loader:
            print(f"⚠️ 无法加载 TTS 模块规范")
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return getattr(module, "CosyVoiceTTS", None)

    def update_session(self, *, pitch_rate: float = 1.0, speech_rate: float = 1.0, volume: int = 50) -> None:
        """Pass segment-level acoustic settings to the underlying CosyVoiceTTS.

        Qwen3-TTS-Flash-Realtime only — silently no-op on non-realtime models.
        Clipping to legal ranges is defensive (spec §5)."""
        if not self.enabled or not self.tts:
            return
        # 直播听感限速：引擎的 HYPER_FLUENT 会推 1.1x，叠加长句会盖掉呼吸停顿
        rate_cap = float(os.getenv("TTS_SPEECH_RATE_CAP", "1.0"))
        pitch_rate  = max(0.7, min(1.3, pitch_rate))
        speech_rate = max(0.7, min(1.3, min(speech_rate, rate_cap)))
        volume      = max(0,   min(100, volume))
        try:
            self.tts.pitch_rate = pitch_rate
            self.tts.speech_rate = speech_rate
            self.tts.volume = volume
        except Exception as exc:
            print(f"[TTS] update_session failed: {exc}")

    def set_instruction(self, instruction: str) -> None:
        """设置下一次合成的自然语言情绪指令（仅 instruct 系列模型生效）。"""
        if not self.enabled or not self.tts:
            return
        try:
            self.tts.instruction = instruction or ""
        except Exception as exc:
            print(f"[TTS] set_instruction failed: {exc}")

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
                future = self._prepare_pool.submit(self._generate_prepared, voice, text)
                self._prepared[key] = future
        return future

    @staticmethod
    def _generate_prepared(voice, text):
        from .breath import synthesize_with_breath
        for attempt in range(2):
            try:
                audio = synthesize_with_breath(voice.synthesize, text, sample_rate=voice.sample_rate)
                if audio:
                    return audio
                raise RuntimeError("TTS returned no audio")
            except Exception:
                if attempt:
                    raise
        raise RuntimeError("TTS failed")

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

        sample_rate = int(os.getenv("TTS_SAMPLE_RATE", "24000"))
        try:
            audio = self.prepare(text).result()
        except Exception as exc:
            print(f"[TTS] 合成错误: {exc}")
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
            print("没有可保存的录制音频")
            return

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from pydub import AudioSegment

            # 使用 pydub 合并所有音频片段
            print(f"  正在合并 {len(self._recording_buffer)} 个音频片段...")

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
                    print(f"    已合并 {i+1}/{len(self._recording_buffer)}...")

            # 导出为 MP3
            if convert_to_mp3:
                mp3_path = str(output_path).replace('.wav', '.mp3') if str(output_path).endswith('.wav') else str(output_path)
                if not mp3_path.endswith('.mp3'):
                    mp3_path += '.mp3'

                print(f"  🔄 正在导出 MP3...")
                combined.export(mp3_path, format="mp3", bitrate="128k")

                # 显示文件大小
                mp3_size = Path(mp3_path).stat().st_size / (1024 * 1024)
                total_wav_size = sum(len(chunk) for chunk in self._recording_buffer) / (1024 * 1024)
                compression_ratio = (1 - mp3_size / total_wav_size) * 100 if total_wav_size > 0 else 0

                print(f"  ✅ MP3 已保存: {Path(mp3_path).name}")
                print(f"  📊 文件大小: {mp3_size:.1f}MB (原始: {total_wav_size:.1f}MB, 压缩率: {compression_ratio:.0f}%)")
            else:
                # 导出为 WAV
                print(f"  🔄 正在导出 WAV...")
                combined.export(str(output_path), format="wav")
                wav_size = output_path.stat().st_size / (1024 * 1024)
                print(f"  ✅ WAV 已保存: {output_path.name} ({wav_size:.1f} MB)")

        except ImportError:
            # 如果 pydub 不可用，使用简单拼接（仅适用于 PCM）
            print("  ⚠️ pydub 不可用，使用简单拼接...")
            with output_path.open("wb") as f:
                for chunk in self._recording_buffer:
                    f.write(chunk)
            wav_size = sum(len(chunk) for chunk in self._recording_buffer)
            print(f"  ✅ 音频已保存: {output_path.name} ({wav_size / (1024*1024):.1f} MB)")

            # 如果需要转换但 pydub 不可用
            if convert_to_mp3:
                convert_wav_to_mp3(str(output_path))

        except Exception as exc:
            print(f"[TTS] 保存录制失败: {exc}")
            import traceback
            traceback.print_exc()

        finally:
            self._recording = False
