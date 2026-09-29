"""
通义千问 TTS Client
支持 Qwen3 实时语音合成与离线语音合成
"""

import base64
import io
import os
import re
import struct
import threading
from datetime import datetime
from typing import Callable, List, Optional


def detect_language(text: str) -> str:
    """
    自动检测文本主要语言。

    Returns:
        "Chinese", "English", "Japanese", "Korean", or "auto"
    """
    if not text:
        return "auto"

    # Count character types
    chinese_count = len(re.findall(r'[\u4e00-\u9fff]', text))
    japanese_count = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', text))  # Hiragana + Katakana
    korean_count = len(re.findall(r'[\uac00-\ud7af]', text))
    english_count = len(re.findall(r'[a-zA-Z]', text))

    total = chinese_count + japanese_count + korean_count + english_count
    if total == 0:
        return "auto"

    # Determine primary language (>40% threshold)
    if japanese_count / total > 0.2:  # Japanese often mixed with kanji
        return "Japanese"
    if korean_count / total > 0.3:
        return "Korean"
    if chinese_count / total > 0.3:
        return "Chinese"
    if english_count / total > 0.4:
        return "English"

    # Mixed content - use auto
    return "auto"


# Multilingual voice mapping - voices that support specific languages
# For Qwen3-TTS-Flash-Realtime / qwen-tts-realtime（与 docs/qwen_tts_voices.md 一致）
QWEN3_TTS_VOICES = {
    # 通义千问3-TTS-Flash-Realtime 支持的全部音色（多语种）
    "multilingual": [
        "Cherry", "Serena", "Ethan", "Chelsie", "Momo", "Vivian", "Moon", "Maia", "Kai",
        "Nofish", "Bella", "Jennifer", "Ryan", "Katerina", "Aiden", "Eldric Sage", "Mia",
        "Mochi", "Bellona", "Vincent", "Bunny", "Neil", "Elias", "Arthur", "Nini", "Ebona",
        "Seren", "Pip", "Stella", "Bodega", "Sonrisa", "Alek", "Dolce", "Sohee", "Ono Anna",
        "Lenn", "Emilien", "Andre", "Radio Gol",
        "Jada", "Dylan", "Li", "Marcus", "Roy", "Peter", "Sunny", "Eric", "Rocky", "Kiki",
    ],
    # English-sounding voices (preferred for English content)
    "english_preferred": [
        "Jennifer", "Ryan", "Dylan", "Marcus", "Eric", "Jada", "Peter",
    ],
    # Chinese-sounding voices (preferred for Chinese content)
    "chinese_preferred": [
        "Cherry", "Li", "Sunny", "Kiki", "Serena", "Bella", "Chelsie",
    ],
}

# For CosyVoice models (cosyvoice-v3-flash, etc.)
COSYVOICE_VOICES = {
    "bilingual": [
        "longanyang", "longanhuan", "longhuhu_v3", "longyingxun",
        "longyingjing_v3", "longxiaochun_v2",
    ],
    "chinese_only": [
        "Bella", "Serena", "Chelsie", "Aura",
    ],
}


def get_multilingual_voice(preferred_voice: str, language: str, model: str = "") -> str:
    """
    获取支持指定语言的音色。

    如果首选音色不支持目标语言，自动切换到支持该语言的音色。

    Args:
        preferred_voice: 首选音色
        language: 目标语言 ("Chinese", "English", "Japanese", "Korean")
        model: TTS 模型名称

    Returns:
        支持目标语言的音色名称
    """
    # Determine model type
    is_qwen3_tts = "qwen" in model.lower() and "tts" in model.lower()

    # For Qwen3-TTS models, all listed voices support multilingual
    if is_qwen3_tts:
        all_voices = [v.lower() for v in QWEN3_TTS_VOICES["multilingual"]]
        if preferred_voice.lower() in all_voices:
            return preferred_voice
        # If voice not in list, use a default multilingual voice
        if language == "English":
            fallback = "Jennifer"  # Good English voice
        else:
            fallback = "Cherry"  # Good general voice
        print(f"[TTS] 音色 {preferred_voice} 不在 Qwen3-TTS 列表中，使用 {fallback}")
        return fallback

    # For CosyVoice models
    if language in ("Chinese", "auto", "Auto"):
        return preferred_voice

    # Check if preferred voice is bilingual
    if preferred_voice.lower() in [v.lower() for v in COSYVOICE_VOICES["bilingual"]]:
        return preferred_voice

    # For non-Chinese languages, switch to a bilingual voice
    if language in ("English", "Japanese", "Korean"):
        if preferred_voice in COSYVOICE_VOICES["chinese_only"]:
            fallback = "longanyang"
            print(f"[TTS] 音色 {preferred_voice} 不支持 {language}，切换到 {fallback}")
            return fallback

    return preferred_voice

# 尝试导入 dashscope
try:
    import dashscope

    from dashscope.audio.tts_v2 import AudioFormat as OfflineAudioFormat
    from dashscope.audio.tts_v2 import ResultCallback, SpeechSynthesizer

    try:
        from dashscope.audio.qwen_tts_realtime import AudioFormat as RealtimeAudioFormat
        from dashscope.audio.qwen_tts_realtime import QwenTtsRealtime, QwenTtsRealtimeCallback

        REALTIME_AVAILABLE = True
    except Exception:
        REALTIME_AVAILABLE = False
        QwenTtsRealtime = None
        QwenTtsRealtimeCallback = object
        RealtimeAudioFormat = None

    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    print("⚠️ dashscope 未安装，请运行: pip install dashscope")


def pcm_to_wav(pcm_data: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """
    将PCM数据转换为WAV格式
    
    Args:
        pcm_data: PCM原始音频数据
        sample_rate: 采样率（默认24000Hz）
        channels: 声道数（默认1=单声道）
        sample_width: 采样位深（默认2字节=16bit）
    
    Returns:
        WAV格式的音频数据
    """
    data_size = len(pcm_data)
    file_size = 36 + data_size
    
    wav_header = struct.pack('<4sI4s4sIHHIIHH4sI',
        b'RIFF',           # ChunkID
        file_size,        # ChunkSize
        b'WAVE',          # Format
        b'fmt ',          # Subchunk1ID
        16,               # Subchunk1Size (PCM)
        1,                # AudioFormat (PCM)
        channels,         # NumChannels
        sample_rate,      # SampleRate
        sample_rate * channels * sample_width,  # ByteRate
        channels * sample_width,  # BlockAlign
        sample_width * 8,  # BitsPerSample
        b'data',          # Subchunk2ID
        data_size         # Subchunk2Size
    )
    
    return wav_header + pcm_data


class TTSCallback(ResultCallback):
    """TTS 流式回调处理"""
    
    def __init__(self, 
                 on_audio: Callable[[bytes], None] = None,
                 save_path: str = None):
        self.on_audio = on_audio
        self.save_path = save_path
        self.audio_buffer = io.BytesIO()
        self.file = None
        self.first_chunk_time = None
        self.start_time = None
        
    def on_open(self):
        self.start_time = datetime.now()
        if self.save_path:
            self.file = open(self.save_path, "wb")
        print(f"[TTS] 连接建立")
    
    def on_data(self, data: bytes):
        if self.first_chunk_time is None:
            self.first_chunk_time = datetime.now()
            latency = (self.first_chunk_time - self.start_time).total_seconds() * 1000
            print(f"[TTS] 首包延迟: {latency:.0f}ms")
        
        # 写入 buffer
        self.audio_buffer.write(data)
        
        # 写入文件
        if self.file:
            self.file.write(data)
        
        # 回调处理
        if self.on_audio:
            self.on_audio(data)
    
    def on_complete(self):
        print(f"[TTS] 合成完成，音频大小: {self.audio_buffer.tell()} bytes")
    
    def on_error(self, message: str):
        print(f"[TTS] 错误: {message}")
    
    def on_close(self):
        if self.file:
            self.file.close()
        print("[TTS] 连接关闭")
    
    def on_event(self, message):
        pass
    
    def get_audio(self) -> bytes:
        """获取完整音频数据"""
        return self.audio_buffer.getvalue()


class QwenRealtimeCallback(QwenTtsRealtimeCallback):
    """Qwen3 Realtime 回调处理"""

    def __init__(self, on_audio: Callable[[bytes], None] = None, save_path: str = None, 
                 response_format: str = "pcm", sample_rate: int = 24000):
        super().__init__()
        self.on_audio = on_audio
        self.save_path = save_path
        self.response_format = response_format.lower()
        self.sample_rate = sample_rate
        self.audio_buffer = io.BytesIO()
        self.file = None
        if save_path:
            # 根据格式决定是否立即打开文件（PCM需要转换，其他格式直接写入）
            if self.response_format == "pcm":
                # PCM格式稍后转换为WAV再保存
                pass
            else:
                self.file = open(save_path, "wb")
        self.complete_event = threading.Event()

    def on_open(self) -> None:
        print("[TTS] Realtime 连接建立")

    def on_close(self, close_status_code, close_msg) -> None:
        # 如果是PCM格式，需要转换为WAV再保存
        if self.save_path and self.response_format == "pcm" and self.audio_buffer.tell() > 0:
            pcm_data = self.audio_buffer.getvalue()
            wav_data = pcm_to_wav(pcm_data, sample_rate=self.sample_rate)
            with open(self.save_path, "wb") as f:
                f.write(wav_data)
            print(f"[TTS] PCM已转换为WAV并保存: {self.save_path} ({len(wav_data)} bytes)")
        elif self.file:
            self.file.close()
        print(f"[TTS] Realtime 连接关闭: {close_status_code}, {close_msg}")

    def on_event(self, response) -> None:
        try:
            if isinstance(response, str):
                import json
                try:
                    response = json.loads(response)
                except:
                    return
            
            event_type = response.get("type")
            # print(f"[TTS] on_event: {event_type}")
            
            if event_type == "response.audio.delta":
                recv_audio_b64 = response.get("delta")
                if recv_audio_b64:
                    chunk = base64.b64decode(recv_audio_b64)
                    self.audio_buffer.write(chunk)
                    if self.file:
                        self.file.write(chunk)
                    if self.on_audio:
                        self.on_audio(chunk)
            
            if event_type in ("response.done", "session.finished"):
                if event_type == "response.done":
                    print(f"[TTS] 收到音频分片完成: {self.audio_buffer.tell()} bytes")
                self.complete_event.set()
            
            if event_type == "error":
                print(f"[TTS] 收到错误事件: {response}")
                self.complete_event.set()
                
        except Exception as exc:
            print(f"[TTS] Realtime 回调错误: {exc}")

    def wait_for_complete(self, timeout: Optional[float] = None) -> bool:
        return self.complete_event.wait(timeout)

    def get_audio(self) -> bytes:
        return self.audio_buffer.getvalue()


class CosyVoiceTTS:
    """
    通义千问 TTS 客户端
    
    使用方法:
        tts = CosyVoiceTTS()
        audio = tts.synthesize("你好，世界！")
    """
    
    # 音频格式映射
    FORMAT_MAP = {
        "mp3": OfflineAudioFormat.MP3_22050HZ_MONO_256KBPS if DASHSCOPE_AVAILABLE else None,
        "wav": OfflineAudioFormat.WAV_22050HZ_MONO_16BIT if DASHSCOPE_AVAILABLE else None,
        "pcm": OfflineAudioFormat.PCM_22050HZ_MONO_16BIT if DASHSCOPE_AVAILABLE else None,
    }
    
    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        voice: str = None,
        audio_format: str = "mp3",
    ):
        """
        初始化 TTS 客户端
        
        Args:
            api_key: DashScope API Key (默认从环境变量读取)
            model: TTS 模型 (默认 qwen3-tts-flash-realtime)
            voice: 音色 (默认 Cherry)
            audio_format: 音频格式 mp3/wav/pcm
        """
        if not DASHSCOPE_AVAILABLE:
            raise ImportError("dashscope 未安装，请运行: pip install dashscope")
        
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 DASHSCOPE_API_KEY 环境变量或传入 api_key 参数")
        
        dashscope.api_key = self.api_key
        
        self.model = model or os.getenv("TTS_MODEL", "qwen3-tts-flash-realtime")
        self.voice = voice or os.getenv("TTS_VOICE", "Cherry")
        self.audio_format = self.FORMAT_MAP.get(audio_format.lower(), self.FORMAT_MAP["mp3"])

        self.mode = os.getenv("TTS_MODE", "server_commit")
        self.language_type = os.getenv("TTS_LANGUAGE_TYPE", "Chinese")
        # 自然语言情绪/语速指令，仅 instruct 系列模型生效（如 qwen3-tts-instruct-flash-realtime）
        self.instruction = ""
        self.response_format = os.getenv("TTS_RESPONSE_FORMAT", "pcm")
        self.sample_rate = int(os.getenv("TTS_SAMPLE_RATE", "24000"))
        self.speech_rate = float(os.getenv("TTS_SPEECH_RATE", "1.0"))
        self.volume = int(os.getenv("TTS_VOLUME", "50"))
        self.pitch_rate = float(os.getenv("TTS_PITCH_RATE", "1.0"))
        self.bit_rate = int(os.getenv("TTS_BIT_RATE", "128"))
        self.timeout = float(os.getenv("TTS_TIMEOUT", "12"))

        region = os.getenv("TTS_REGION", "cn").lower()
        self.ws_url = os.getenv("TTS_WS_URL")
        if not self.ws_url:
            self.ws_url = (
                "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
                if region in ("sg", "intl", "international")
                else "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
            )

        realtime = "realtime" in self.model
        if realtime and not REALTIME_AVAILABLE:
            raise ImportError("dashscope 版本过低，需 >= 1.25.2 才能使用 QwenTTS Realtime")

        print(f"✅ TTS Client 初始化: model={self.model}, voice={self.voice}, mode={self.mode}")
    
    def _is_realtime_model(self) -> bool:
        return "realtime" in self.model

    def _resolve_realtime_audio_format(self):
        if not RealtimeAudioFormat:
            return None
        fmt = self.response_format.lower()
        sample_rate = self.sample_rate
        
        # Qwen3 Realtime 常见的 MP3 枚举名通常是 MP3_22050HZ_MONO_256KBPS 等
        # 我们根据实际返回的可用枚举进行匹配
        if fmt == "pcm":
            name = f"PCM_{sample_rate}HZ_MONO_16BIT"
            return getattr(RealtimeAudioFormat, name, RealtimeAudioFormat.PCM_24000HZ_MONO_16BIT)
        if fmt == "wav":
            name = f"WAV_{sample_rate}HZ_MONO_16BIT"
            return getattr(RealtimeAudioFormat, name, RealtimeAudioFormat.PCM_24000HZ_MONO_16BIT)
        if fmt == "mp3":
            # 尝试几种常见的 Qwen Realtime MP3 枚举名
            for rate in [sample_rate, 22050, 24000]:
                for kbps in [256, 128]:
                    name = f"MP3_{rate}HZ_MONO_{kbps}KBPS"
                    if hasattr(RealtimeAudioFormat, name):
                        return getattr(RealtimeAudioFormat, name)
            # 兜底：如果找不到 MP3 枚举，报错或返回默认 PCM
            print(f"⚠️ 找不到匹配的 MP3 枚举名，将使用默认 PCM 格式")
            return RealtimeAudioFormat.PCM_24000HZ_MONO_16BIT
        if fmt == "opus":
            name = f"OPUS_{sample_rate}HZ_MONO_128KBPS"
            return getattr(RealtimeAudioFormat, name, RealtimeAudioFormat.PCM_24000HZ_MONO_16BIT)
        return RealtimeAudioFormat.PCM_24000HZ_MONO_16BIT

    def _synthesize_realtime(self, text: str, save_path: str = None) -> bytes:
        callback = QwenRealtimeCallback(
            save_path=save_path,
            response_format=self.response_format,
            sample_rate=self.sample_rate
        )
        qwen_tts_realtime = QwenTtsRealtime(
            model=self.model,
            callback=callback,
            url=self.ws_url,
        )
        try:
            qwen_tts_realtime.connect()

            # Auto-detect language if set to "auto" or empty
            language = self.language_type
            if language.lower() in ("auto", ""):
                language = detect_language(text)
                if language == "auto":
                    language = "Chinese"  # Fallback

            # Auto-select voice that supports the detected language
            voice = get_multilingual_voice(self.voice, language, self.model)

            response_format = self._resolve_realtime_audio_format()
            kwargs = {
                "voice": voice,
                "response_format": response_format,
                "mode": self.mode,
                "language_type": language,
            }
            # 只有qwen3-tts-flash-realtime支持这些参数，旧版qwen-tts-realtime不支持
            if "qwen3" in self.model:
                kwargs.update({
                    "speech_rate": self.speech_rate,
                    "volume": self.volume,
                    "pitch_rate": self.pitch_rate,
                })
            if self.response_format.lower() == "opus":
                kwargs["bit_rate"] = self.bit_rate
            if "instruct" in self.model and self.instruction:
                kwargs["instructions"] = self.instruction

            qwen_tts_realtime.update_session(**kwargs)
            qwen_tts_realtime.append_text(text)
            if self.mode == "commit":
                qwen_tts_realtime.commit()
            qwen_tts_realtime.finish()

            completed = callback.wait_for_complete(self.timeout)
        finally:
            qwen_tts_realtime.close()
        if not completed:
            print("[TTS] 等待音频超时，本句保留字幕")
            return b""
        
        audio_data = callback.get_audio()
        # 如果返回的是PCM，转换为WAV
        if self.response_format.lower() == "pcm" and audio_data:
            audio_data = pcm_to_wav(audio_data, sample_rate=self.sample_rate)
        
        return audio_data

    def _synthesize_offline(self, text: str, save_path: str = None) -> bytes:
        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self.audio_format,
        )
        audio = synthesizer.call(text)
        if save_path:
            with open(save_path, "wb") as f:
                f.write(audio)
            print(f"[TTS] 音频已保存: {save_path}")
        return audio

    def synthesize(self, text: str, save_path: str = None) -> bytes:
        """
        非流式语音合成
        
        Args:
            text: 待合成文本
            save_path: 保存路径 (可选)
        
        Returns:
            音频二进制数据
        """
        if self._is_realtime_model():
            return self._synthesize_realtime(text, save_path)
        return self._synthesize_offline(text, save_path)
    
    def synthesize_streaming(self,
                            texts: List[str],
                            save_path: str = None,
                            on_audio: Callable[[bytes], None] = None) -> bytes:
        """
        双向流式语音合成
        
        Args:
            texts: 文本片段列表
            save_path: 保存路径 (可选)
            on_audio: 音频数据回调函数
        
        Returns:
            完整音频二进制数据
        """
        if self._is_realtime_model():
            joined = "".join(texts)
            return self._synthesize_realtime(joined, save_path)

        callback = TTSCallback(on_audio=on_audio, save_path=save_path)

        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self.audio_format,
            callback=callback,
        )

        # 流式发送文本
        for text in texts:
            if text.strip():
                synthesizer.streaming_call(text)

        # 完成合成
        synthesizer.streaming_complete()

        return callback.get_audio()
    
    def synthesize_with_callback(self,
                                text: str,
                                save_path: str = None,
                                on_audio: Callable[[bytes], None] = None) -> bytes:
        """
        单向流式语音合成（一次性发送文本，流式接收音频）
        
        Args:
            text: 待合成文本
            save_path: 保存路径 (可选)
            on_audio: 音频数据回调函数
        
        Returns:
            完整音频二进制数据
        """
        if self._is_realtime_model():
            return self._synthesize_realtime(text, save_path)

        callback = TTSCallback(on_audio=on_audio, save_path=save_path)

        synthesizer = SpeechSynthesizer(
            model=self.model,
            voice=self.voice,
            format=self.audio_format,
            callback=callback,
        )

        synthesizer.call(text)

        return callback.get_audio()


# ============================================
# 便捷函数
# ============================================

def text_to_speech(text: str, 
                   save_path: str = None,
                   voice: str = None,
                   model: str = None) -> bytes:
    """
    快速文本转语音
    
    Args:
        text: 待合成文本
        save_path: 保存路径
        voice: 音色
        model: 模型
    
    Returns:
        音频二进制数据
    """
    tts = CosyVoiceTTS(voice=voice, model=model)
    return tts.synthesize(text, save_path)


# ============================================
# 测试
# ============================================

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()

    print("\n=== Qwen TTS 测试 ===\n")

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("❌ 请设置 DASHSCOPE_API_KEY 环境变量")
        print("   在 .env 文件中添加: DASHSCOPE_API_KEY=your-api-key")
        exit(1)

    try:
        tts = CosyVoiceTTS()

        print("\n[测试] 语音合成...")
        audio = tts.synthesize(
            "你好，我是AI主播小助手，很高兴认识你！",
            save_path="test_output.bin",
        )
        print(f"  生成音频大小: {len(audio)} bytes")

        print("\n✅ 测试完成！")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
