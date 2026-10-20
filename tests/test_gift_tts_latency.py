import base64

from echuu.config import TTSConfig
from echuu.live import qwen_tts


def test_realtime_waits_for_updated_configuration_before_sending_text(monkeypatch):
    seen = []

    class Request:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def connect(self):
            pass

        def update_session(self, **kwargs):
            callback = self.callback

            class Ready:
                def wait(self, timeout):
                    assert timeout > 0
                    seen.append("configured")
                    return True

                def is_set(self):
                    return "configured" in seen

            callback.session_ready_event = Ready()

        def append_text(self, text):
            assert self.callback.session_ready_event.is_set()
            seen.append("text")

        def finish(self):
            self.callback.on_event(
                {
                    "type": "response.audio.delta",
                    "delta": base64.b64encode(b"audio").decode(),
                }
            )
            self.callback.on_event({"type": "response.done"})

        def close(self):
            self.callback.on_close(1000, "closed")
            seen.append("closed")

    monkeypatch.setattr(qwen_tts, "QwenTtsRealtime", Request)
    voice = qwen_tts.CosyVoiceTTS(config=TTSConfig(api_key="fixture"))
    assert voice.synthesize("测试").endswith(b"audio")
    assert seen == ["configured", "text", "closed"]


def test_missing_configuration_ack_does_not_send_text(monkeypatch):
    seen = []

    class Request:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def connect(self):
            pass

        def update_session(self, **kwargs):
            pass

        def append_text(self, text):
            raise AssertionError("configuration not acknowledged")

        def close(self):
            seen.append("closed")
            self.callback.on_close(1000, "closed")

    monkeypatch.setattr(qwen_tts, "QwenTtsRealtime", Request)
    voice = qwen_tts.CosyVoiceTTS(config=TTSConfig(api_key="fixture", timeout=0.01))
    assert voice.synthesize("测试") == b""
    assert seen == ["closed"]


def test_premature_close_and_provider_error_never_accept_partial_audio():
    for event in ("close", "error"):
        callback = qwen_tts.QwenRealtimeCallback()
        callback.on_event(
            {
                "type": "response.audio.delta",
                "delta": base64.b64encode(b"partial").decode(),
            }
        )
        if event == "close":
            callback.on_close(1006, "unexpected")
        else:
            callback.on_event({"type": "error"})
        assert callback.complete_event.is_set()
        assert callback.failed
