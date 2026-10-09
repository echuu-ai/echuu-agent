import importlib


def test_legacy_tts_import_reexports_same_class():
    from echuu.live.qwen_tts import CosyVoiceTTS

    legacy = importlib.import_module("workflow.backend.tts_client")
    assert legacy.CosyVoiceTTS is CosyVoiceTTS
