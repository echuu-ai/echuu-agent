"""Compatibility import; the implementation ships inside the echuu package."""

from echuu.live.qwen_tts import (
    COSYVOICE_VOICES,
    QWEN3_TTS_VOICES,
    CosyVoiceTTS,
    QwenRealtimeCallback,
    TTSCallback,
    detect_language,
    get_multilingual_voice,
    pcm_to_wav,
    text_to_speech,
)

__all__ = [
    "COSYVOICE_VOICES",
    "QWEN3_TTS_VOICES",
    "CosyVoiceTTS",
    "QwenRealtimeCallback",
    "TTSCallback",
    "detect_language",
    "get_multilingual_voice",
    "pcm_to_wav",
    "text_to_speech",
]

if __name__ == "__main__":
    import runpy

    runpy.run_module("echuu.live.qwen_tts", run_name="__main__")
