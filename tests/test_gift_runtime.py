import json
from concurrent.futures import Future
from types import SimpleNamespace

from echuu.gift_runtime import GiftRuntime
from tests.test_session_extensions import batch


def rig():
    lines = [SimpleNamespace(id=str(i), text=f"原句{i}。") for i in range(2)]
    response = {
        "selected_gift_id": "baozi",
        "acknowledgement": "谢谢大家的包子！",
        "sentences": ["第一句暖和一点。", "第二句保持轻松。"],
    }

    class TTS:
        enabled = True

        def synthesize(self, text):
            return b"audio"

        def prepare(self, text):
            result = Future()
            result.set_result(b"audio")
            return result

        def fork_preparation(self):
            return self

        def close_preparation(self):
            pass

    engine = SimpleNamespace(
        state=SimpleNamespace(persona="温暖", topic="日常", spoken_history=[]),
        llm_gen=SimpleNamespace(generate=lambda _: json.dumps(response)),
        tts=TTS(),
        background_interactions=SimpleNamespace(revision=0),
        _interaction_audio={},
        _upcoming_script_lines=lambda: [
            (None, i, line) for i, line in enumerate(lines)
        ],
    )
    return GiftRuntime(engine), engine, lines, response


def test_effect_is_staged_until_thanks_played_and_restores_on_next_batch():
    runtime, engine, lines, _ = rig()
    try:
        runtime.prepare(batch(), True)
        assert lines[0].text == "原句0。"
        runtime.finish("gift", False)
        assert lines[0].text == "原句0。"
        runtime.prepare(batch("next"), True)
        runtime.finish("next", True)
        assert lines[0].text == "第一句暖和一点。"
        assert engine._interaction_audio
        runtime.prepare(batch("preview"), False)
        assert lines[0].text == "原句0。"
    finally:
        runtime.close()


def test_invalid_selection_and_stale_revision_keep_original():
    runtime, engine, lines, response = rig()
    try:
        response["selected_gift_id"] = "forged"
        text, _ = runtime.prepare(batch(), True)
        assert text == "谢谢大家送来的礼物，心意都收到啦！"
        runtime.finish("gift", True)
        assert lines[0].text == "原句0。"
        response["selected_gift_id"] = "baozi"
        runtime.prepare(batch("next"), True)
        engine.background_interactions.revision += 1
        runtime.finish("next", True)
        assert lines[0].text == "原句0。"
    finally:
        runtime.close()
