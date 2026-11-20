import json
from types import SimpleNamespace

import pytest

from echuu.prepared_rundown import RundownValidationError, generate_sections
from tests.test_prepared_story import full_story


def test_invalid_rundown_is_repaired_with_identical_limits_and_no_audio():
    artifact = full_story()
    good = {key: artifact.draft[key] for key in ("opening", "closing")}
    bad = {**good, "closing": []}
    prompts = []
    responses = iter([json.dumps(bad), json.dumps(good)])

    def call(prompt, **_):
        prompts.append(prompt)
        return next(responses)

    engine = SimpleNamespace(llm=SimpleNamespace(call=call))
    assert (
        generate_sections(engine, artifact.inputs, artifact.background, artifact.draft)
        == good
    )
    assert len(prompts) == 2 and "section_count" in prompts[1]
    assert [bool(row["issues"]) for row in engine._prepared_rundown_trace] == [
        True,
        False,
    ]


def test_invalid_rundown_exhaustion_never_publishes_partial_full_script():
    artifact = full_story()
    engine = SimpleNamespace(
        llm=SimpleNamespace(call=lambda *_, **__: '{"opening":[],"closing":[]}')
    )
    with pytest.raises(RundownValidationError):
        generate_sections(engine, artifact.inputs, artifact.background, artifact.draft)
    assert len(engine._prepared_rundown_trace) == 3
    assert all(row["issues"] for row in engine._prepared_rundown_trace)


def test_japanese_selection_rejects_chinese_body_and_sections():
    from echuu.generators.shareable_clip import validate_draft
    from echuu.prepared_rundown import validate_sections
    artifact = full_story()
    inputs = artifact.inputs.model_copy(update={"language": "ja"})
    issues = validate_sections(artifact.draft, inputs, artifact.background)
    assert any(issue["code"] == "wrong_language" for issue in issues)
    issues = validate_draft(artifact.draft, {"min_chars":264, "max_chars":506}, {"language":"ja"})
    assert any(issue["code"] == "wrong_language" for issue in issues)
