from types import SimpleNamespace

import pytest


class FakeLLM:
    model = "offline-fixture"

    def call(self, prompt, system=None, max_tokens=1000):
        raise AssertionError("Construction must not call the model")


def test_normal_constructor_needs_no_checkout_or_files(tmp_path, monkeypatch):
    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHUU_LLM_PROVIDER", "invalid-provider")
    engine = EchuuLiveEngine(config=EngineConfig(), llm=FakeLLM())
    try:
        assert engine.llm.model == "offline-fixture"
        assert not engine.tts.enabled
        assert engine.example_sampler is None
        assert list(tmp_path.iterdir()) == []
    finally:
        engine.tts.close_preparation()


def test_constructor_does_not_load_dotenv(tmp_path, monkeypatch):
    import dotenv

    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig

    def forbidden(*args, **kwargs):
        raise AssertionError("Library must not load environment files")

    monkeypatch.setattr(dotenv, "load_dotenv", forbidden)
    monkeypatch.chdir(tmp_path)
    engine = EchuuLiveEngine(config=EngineConfig(), llm=FakeLLM())
    engine.tts.close_preparation()


def test_explicit_paths_and_no_default_export(tmp_path):
    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig
    from tests.test_danmaku_interleave import _make_show

    engine = EchuuLiveEngine(config=EngineConfig(), llm=FakeLLM())
    try:
        assert engine._save_script_v5(_make_show(), "fixture", "topic") is None
    finally:
        engine.tts.close_preparation()
    output = tmp_path / "chosen-output"
    engine = EchuuLiveEngine(config=EngineConfig(output_dir=output), llm=FakeLLM())
    try:
        assert not output.exists()
        engine._save_script_v5(_make_show(), "fixture", "topic")
        assert len(list(output.glob("*.json"))) == 1
    finally:
        engine.tts.close_preparation()


def test_explicit_missing_examples_fail_clearly(tmp_path):
    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig

    with pytest.raises(FileNotFoundError):
        EchuuLiveEngine(
            config=EngineConfig(examples_path=tmp_path / "missing.jsonl"), llm=FakeLLM()
        )


def test_tts_is_a_package_module():
    from echuu.live.tts_client import TTSClient

    cls = TTSClient.__new__(TTSClient)._load_cosyvoice_class()
    assert cls.__module__ == "echuu.live.qwen_tts"


def test_two_explicit_voices_ignore_environment(monkeypatch):
    from echuu.config import TTSConfig
    from echuu.live.tts_client import TTSClient

    monkeypatch.setenv("TTS_VOICE", "environment-voice")
    monkeypatch.setenv("TTS_SAMPLE_RATE", "99999")
    clients = [
        TTSClient(config=TTSConfig(api_key="fixture", voice=v))
        for v in ("Cherry", "Serena")
    ]
    try:
        assert [c.tts.voice for c in clients] == ["Cherry", "Serena"]
        assert all(c.tts.sample_rate == 24000 for c in clients)
        import os

        assert os.environ["TTS_VOICE"] == "environment-voice"
    finally:
        for client in clients:
            client.close_preparation()


def test_explicit_disabled_tts_does_not_use_environment_key(monkeypatch):
    from echuu.config import TTSConfig
    from echuu.live.tts_client import TTSClient

    monkeypatch.setenv("DASHSCOPE_API_KEY", "fixture-environment-key")
    client = TTSClient(config=TTSConfig())
    try:
        assert not client.enabled
    finally:
        client.close_preparation()


def test_environment_compatibility_is_explicit(monkeypatch):
    from echuu.config import TTSConfig

    monkeypatch.setenv("TTS_VOICE", "Serena")
    monkeypatch.setenv("TTS_TIMEOUT", "8")
    assert TTSConfig.from_env().voice == "Serena"
    assert TTSConfig.from_env().timeout == 8
    assert TTSConfig().voice == "Cherry"


def test_injected_tts_bypasses_default_creation(monkeypatch):
    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig
    from echuu.live import engine as module

    def forbidden(*args, **kwargs):
        raise AssertionError("Must use caller's TTS")

    monkeypatch.setattr(module, "TTSClient", forbidden)
    speech = SimpleNamespace(enabled=False)
    engine = EchuuLiveEngine(config=EngineConfig(), llm=FakeLLM(), tts=speech)
    assert engine.tts is speech


def test_explicit_provider_receives_configured_model_and_key(monkeypatch):
    from echuu import EchuuLiveEngine
    from echuu.config import EngineConfig
    from echuu.live import engine as module

    calls = []
    monkeypatch.setattr(
        module, "create_llm_client", lambda **kw: calls.append(kw) or FakeLLM()
    )
    engine = EchuuLiveEngine(
        config=EngineConfig(provider="qwen", model="chosen", api_key="fixture")
    )
    try:
        assert calls[0]["model"] == "chosen"
        assert calls[0]["api_key"] == "fixture"
    finally:
        engine.tts.close_preparation()


def test_explicit_config_hides_key_in_repr():
    from echuu.config import EngineConfig, TTSConfig

    assert "private-fixture" not in repr(EngineConfig(api_key="private-fixture"))
    assert "private-fixture" not in repr(TTSConfig(api_key="private-fixture"))


def test_provider_override_selects_matching_environment_credentials(monkeypatch):
    from echuu.config import EngineConfig

    monkeypatch.setenv("DASHSCOPE_API_KEY", "qwen-fixture")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "claude-fixture")
    config = EngineConfig.from_env(provider="claude")
    assert config.provider == "claude"
    assert config.api_key.get_secret_value() == "claude-fixture"


def test_tts_construction_keeps_sdk_global_credential(monkeypatch):
    import dashscope

    from echuu.config import TTSConfig
    from echuu.live.qwen_tts import CosyVoiceTTS

    monkeypatch.setattr(dashscope, "api_key", "process-fixture")
    first = CosyVoiceTTS(config=TTSConfig(api_key="first", voice="Cherry"))
    second = CosyVoiceTTS(config=TTSConfig(api_key="second", voice="Serena"))
    assert (first.api_key, second.api_key) == ("first", "second")
    assert dashscope.api_key == "process-fixture"


def test_offline_constructor_restores_global_even_on_failure(monkeypatch):
    from echuu.config import TTSConfig
    from echuu.live import qwen_tts

    monkeypatch.setattr(qwen_tts.dashscope, "api_key", "process-fixture")
    voice = qwen_tts.CosyVoiceTTS(config=TTSConfig(api_key="instance-fixture"))

    def fail(**kwargs):
        assert qwen_tts.dashscope.api_key == "instance-fixture"
        raise RuntimeError("constructor failure")

    monkeypatch.setattr(qwen_tts, "SpeechSynthesizer", fail)
    with pytest.raises(RuntimeError, match="constructor failure"):
        voice._create_offline_synthesizer(model="fixture")
    assert qwen_tts.dashscope.api_key == "process-fixture"


def test_realtime_request_binds_own_key(monkeypatch):
    from echuu.config import TTSConfig
    from echuu.live import qwen_tts

    seen = []

    class Request:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def connect(self):
            seen.append(self.apikey)
            raise RuntimeError("stop before network")

        def close(self):
            seen.append("closed")

    monkeypatch.setattr(qwen_tts, "QwenTtsRealtime", Request)
    voice = qwen_tts.CosyVoiceTTS(config=TTSConfig(api_key="instance-fixture"))
    with pytest.raises(RuntimeError, match="stop before network"):
        voice.synthesize("测试")
    assert seen == ["instance-fixture", "closed"]


def test_explicit_custom_voice_is_never_silently_substituted(monkeypatch):
    from echuu.config import TTSConfig
    from echuu.live import qwen_tts

    voices = []

    class Request:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            pass

        def update_session(self, **kwargs):
            voices.append(kwargs["voice"])
            raise RuntimeError("stop before synthesis")

        def close(self):
            pass

    monkeypatch.setattr(qwen_tts, "QwenTtsRealtime", Request)
    client = qwen_tts.CosyVoiceTTS(
        config=TTSConfig(api_key="fixture", voice="custom-voice-id")
    )
    with pytest.raises(RuntimeError, match="stop before synthesis"):
        client.synthesize("hello")
    assert voices == ["custom-voice-id"]
