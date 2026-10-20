import json
import threading
from types import SimpleNamespace

import pytest

from tests.test_gift_runtime import rig
from tests.test_session_extensions import batch


def test_bounds_feedback_repairs_embedded_question_without_normalizing_output():
    runtime, engine, lines, valid = rig()
    invalid = {**valid, "sentences": ["弹幕问这是什么？我当场愣住了。", valid["sentences"][1]]}
    prompts = []

    def generate(prompt):
        prompts.append(prompt)
        return json.dumps(invalid if len(prompts) == 1 else valid)

    engine.llm_gen.generate = generate
    try:
        runtime.prepare(batch(), True)
        item = runtime.snapshot()[0]
        assert len(prompts) == 2
        assert "multiple_sentences" in prompts[1]
        assert invalid["sentences"][0] in prompts[1]
        assert [r["status"] for r in item["model_attempts"]] == ["failed", "passed"]
        issue = item["model_attempts"][0]["issues"][0]
        assert issue == dict(field="sentences", code="multiple_sentences", position=1,
                             chars=len(invalid["sentences"][0]), max_chars=160,
                             segments=2, max_segments=1)
        assert item["model_reason"] is None
        assert "弹幕" not in json.dumps(item, ensure_ascii=False)
        assert lines[0].text == "原句0。"
        runtime.finish("gift", True)
        assert lines[0].text == valid["sentences"][0]
    finally:
        runtime.close()


@pytest.mark.parametrize("field,text,code,position", [
    ("acknowledgement", "谢谢！收到啦！", "multiple_sentences", None),
    ("sentences", "长" * 161, "string_too_long", 1),
    ("sentences", "  ", "string_too_short", 1),
])
def test_failed_repair_retains_precise_bounds_and_original(field, text, code, position):
    runtime, engine, lines, response = rig()
    if field == "sentences":
        response[field][0] = text
    else:
        response[field] = text
    calls = []
    engine.llm_gen.generate = lambda prompt: calls.append(prompt) or json.dumps(response)
    try:
        runtime.prepare(batch(), True)
        runtime.finish("gift", True)
        item = runtime.snapshot()[0]
        assert len(calls) == 2
        assert item["reason"] == "sentence_bounds"
        assert [x["status"] for x in item["model_attempts"]] == ["failed", "failed"]
        assert all(x["issues"][0]["code"] == code for x in item["model_attempts"])
        assert item["validation_issues"][0]["position"] == position
        assert lines[0].text == "原句0。"
        assert item["changed_lines"] == 0
    finally:
        runtime.close()


def test_repair_result_must_pass_content_guard_again(monkeypatch):
    import echuu.gift_runtime as module
    runtime, engine, lines, valid = rig()
    outputs = iter([
        {**valid, "acknowledgement": "谢谢！收到啦！"},
        {**valid, "sentences": ["不可信新事实。", valid["sentences"][1]]},
    ])
    engine.llm_gen.generate = lambda _: json.dumps(next(outputs))
    monkeypatch.setattr(module, "approve_reply", lambda _, text: "不可信" not in text)
    try:
        runtime.prepare(batch(), True)
        item = runtime.snapshot()[0]
        assert item["reason"] == "content_guard"
        assert [x["reason"] for x in item["model_attempts"]] == ["sentence_bounds", "content_guard"]
        runtime.finish("gift", True)
        assert lines[0].text == "原句0。"
    finally:
        runtime.close()


def test_late_repair_is_sealed_and_never_changes_live_content():
    runtime, engine, lines, valid = rig()
    engine.config = SimpleNamespace(gift_model_timeout_seconds=0.03)
    release, returned = threading.Event(), threading.Event()
    calls = []

    def generate(prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({**valid, "acknowledgement": "谢谢！收到啦！"})
        release.wait(2)
        returned.set()
        return json.dumps(valid)

    engine.llm_gen.generate = generate
    try:
        runtime.prepare(batch(), True)
        item = runtime.snapshot()[0]
        assert item["reason"] == "model_timeout"
        assert [x["status"] for x in item["model_attempts"]] == ["failed", "timeout"]
        release.set()
        assert returned.wait(1)
        with pytest.raises(TimeoutError):
            runtime.future.result(timeout=1)
        assert runtime.snapshot()[0] == item
        runtime.finish("gift", True)
        assert lines[0].text == "原句0。"
    finally:
        release.set()
        runtime.close()
