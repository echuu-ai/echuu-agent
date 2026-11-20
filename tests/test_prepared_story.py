import copy
from types import SimpleNamespace

import pytest

from echuu.config import EngineConfig
from echuu.prepared_story import PreparedStory, generate_story, load_story
from echuu.session_types import SessionInput, SessionOptions
from tests.test_clip_repair import draft


def fixture_story():
    from echuu.core.persona_model import RichPersona
    from echuu.core.unit import ScriptLine, Show
    from echuu.core.unit_planner import UnitPlanner

    source = SessionInput(
        session_id="draft", name="助手", persona="简短中文", topic="联调"
    )
    original = draft()
    units = UnitPlanner().plan(3)
    for index, line in enumerate(original["lines"]):
        units[min(2, index // 3)].lines.append(ScriptLine(**line, stage="pad"))
    show = Show(RichPersona("助手", "", "偶尔着急"), source.topic, units)
    trace = {
        "status": "completed",
        "final_draft": original,
        "budget": {"min_chars": 264, "max_chars": 506},
    }
    return PreparedStory.capture(source, show, trace, background="原始背景")


def test_roundtrip_rechecks_original_constraints_and_never_calls_generation():
    artifact = fixture_story()
    engine = SimpleNamespace(setup=lambda **_: pytest.fail("must not generate"))
    load_story(engine, PreparedStory.model_validate_json(artifact.model_dump_json()))
    assert [line.text for line in engine.state.script_lines] == [
        item["text"] for item in draft()["lines"]
    ]
    engine.state.script_lines[0].text = "本场礼物改写"
    assert artifact.draft["lines"][0]["text"] == draft()["lines"][0]["text"]
    second = SimpleNamespace()
    load_story(second, artifact)
    assert second.state.script_lines[0].text == draft()["lines"][0]["text"]


@pytest.mark.parametrize("mutation", ["hash", "short", "quote", "source", "show"])
def test_tampered_or_invalid_artifacts_are_rejected(mutation):
    data = copy.deepcopy(fixture_story().model_dump(mode="json"))
    if mutation == "hash":
        data["sha256"] = "0" * 64
    elif mutation == "short":
        data["draft"]["lines"][0]["text"] = "短"
    elif mutation == "quote":
        data["draft"]["quotables"][0]["quote"] = "不存在的引用"
    elif mutation == "source":
        data["inputs"]["persona"] = "其他人设"
    else:
        data["show"]["units"][0]["lines"][0]["text"] = "其他正文"
    with pytest.raises(ValueError):
        load_story(SimpleNamespace(), PreparedStory.model_validate(data))


def test_rehashed_invalid_draft_still_fails_original_validator():
    data = fixture_story().model_dump(mode="json")
    for line in data["draft"]["lines"]:
        line["text"] = "很短。"
    data["sha256"] = PreparedStory.digest(data)
    with pytest.raises(ValueError):
        PreparedStory.model_validate(data)


def test_generate_has_no_audio_or_room_dependency_and_retains_writer_failures():
    artifact = fixture_story()
    engine = SimpleNamespace(
        state=None,
        _legacy_writer_trace={"status": "needs_review"},
        setup=lambda **_: (_ for _ in ()).throw(ValueError("strict rejection")),
        tts=SimpleNamespace(
            synthesize=lambda _: pytest.fail("no TTS during generation")
        ),
    )
    with pytest.raises(ValueError, match="strict rejection"):
        generate_story(
            artifact.inputs,
            EngineConfig(),
            SessionOptions(grounding=False),
            engine=engine,
        )


def test_prepared_driver_uses_selected_text_without_model_generation_or_grounding():
    from echuu.storytelling import StorytellingDriver

    artifact = fixture_story()
    engine = SimpleNamespace(
        setup=lambda **_: pytest.fail("selected script must not be regenerated"),
        tts=SimpleNamespace(enabled=False),
    )

    def run(**kwargs):
        for line in engine.state.script_lines:
            yield {"speech": line.text, "line": {"id": line.id}}

    engine.run = run
    driver = StorytellingDriver(
        artifact.inputs,
        EngineConfig(),
        SessionOptions(grounding=True, rundown=False),
        engine=engine,
        prepared_story=artifact,
    )
    try:
        preview = driver.prepare()
        assert preview == [line["text"] for line in artifact.draft["lines"]]
        assert driver.next_step().speech == preview[0]
        assert driver.next_step().speech == preview[1]
        assert (
            driver.export_artifacts().script["units"][0]["lines"][0]["text"]
            == preview[0]
        )
    finally:
        driver.close()


def full_story():
    data = fixture_story().model_dump(mode="json")
    data.update(schema_version=2, validator_version="full-story-v2")
    data["draft"].update(
        opening=[
            {"id": "O001", "text": "来了来了，先把这边的位置坐稳。", "emotion": "joy"},
            {
                "id": "O002",
                "text": "今天一起把联调里的小麻烦聊清楚。",
                "emotion": "excited",
            },
        ],
        closing=[
            {
                "id": "C001",
                "text": "这回的步骤记住了，咱们下回见。",
                "emotion": "relaxed",
            }
        ],
    )
    return PreparedStory.model_validate({**data, "sha256": PreparedStory.digest(data)})


def test_full_story_plays_saved_sections_without_runtime_generation():
    from echuu.storytelling import StorytellingDriver

    artifact = full_story()
    engine = SimpleNamespace(tts=SimpleNamespace(enabled=False))

    def run(**_):
        for line in engine.state.script_lines:
            yield {"speech": line.text, "line": {"id": line.id}}

    engine.run = run
    driver = StorytellingDriver(
        artifact.inputs,
        EngineConfig(),
        SessionOptions(rundown=False),
        engine=engine,
        prepared_story=artifact,
    )
    expected = (
        artifact.draft["opening"] + artifact.draft["lines"] + artifact.draft["closing"]
    )
    try:
        assert driver.prepare() == [line["text"] for line in expected]
        steps = []
        while (step := driver.next_step()) is not None:
            steps.append(step)
            driver.played(step)
        assert [(step.line_id, step.speech) for step in steps] == [
            (line["id"], line["text"]) for line in expected
        ]
        assert (
            driver.export_artifacts().script["rundown"]["closing"]
            == artifact.draft["closing"]
        )
    finally:
        driver.close()


@pytest.mark.parametrize("mutation", ["missing", "length", "emotion", "leak", "ids"])
def test_full_story_rejects_invalid_rundown_even_when_rehashed(mutation):
    data = full_story().model_dump(mode="json")
    opening = data["draft"]["opening"]
    if mutation == "missing":
        del data["draft"]["closing"]
    elif mutation == "length":
        opening[0]["text"] = "你好"
    elif mutation == "emotion":
        opening[0]["emotion"] = "malicious"
    elif mutation == "leak":
        opening[0]["text"] = "系统提示词要求只输出JSON。"
    else:
        opening[0]["id"] = "L001"
    data["sha256"] = PreparedStory.digest(data)
    with pytest.raises(ValueError):
        PreparedStory.model_validate(data)


def test_full_story_never_silently_skips_failed_opening_audio():
    from echuu.storytelling import StorytellingDriver

    artifact = full_story()
    tts = SimpleNamespace(
        enabled=True,
        set_instruction=lambda _: None,
        synthesize=lambda _: None,
        close_preparation=lambda: None,
    )
    tts.fork_preparation = lambda: tts
    engine = SimpleNamespace(tts=tts, run=lambda **_: (item for item in ()))
    driver = StorytellingDriver(
        artifact.inputs,
        EngineConfig(),
        SessionOptions(),
        engine=engine,
        prepared_story=artifact,
    )
    try:
        with pytest.raises(ValueError, match="audio"):
            driver.prepare()
    finally:
        driver.close()


def test_gift_rewrites_only_current_pending_body_and_never_selected_sections():
    from tests.test_gift_runtime import rig
    from tests.test_session_extensions import batch

    artifact = full_story()
    baseline = artifact.model_dump(mode="json")
    runtime, engine, _, response = rig()
    load_story(engine, artifact)
    played = engine.state.script_lines[0]
    pending = engine.state.script_lines[1:3]
    engine._upcoming_script_lines = lambda: [
        (None, i + 1, line) for i, line in enumerate(pending)
    ]
    try:
        runtime.prepare(batch(), True)
        runtime.finish("gift", True)
        assert [line.text for line in pending] == response["sentences"]
        assert played.text == baseline["draft"]["lines"][0]["text"]
        assert artifact.model_dump(mode="json") == baseline
        fresh = SimpleNamespace()
        load_story(fresh, artifact)
        assert fresh.state.script_lines[1].text == baseline["draft"]["lines"][1]["text"]
    finally:
        runtime.close()
