import hashlib
import json
from dataclasses import asdict
from typing import Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from .session_types import Contract, SessionInput, SessionOptions


class PreparedStory(Contract):
    schema_version: Literal[1, 2] = 1
    validator_version: Literal["shareable-clip-v1", "full-story-v2"] = (
        "shareable-clip-v1"
    )
    inputs: SessionInput
    background: str
    show: dict[str, JsonValue]
    draft: dict[str, JsonValue]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @staticmethod
    def digest(data):
        value = {key: item for key, item in data.items() if key != "sha256"}
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()

    @model_validator(mode="after")
    def validate_content(self):
        from .core.persona_card import PersonaCard
        from .core.persona_model import RichPersona
        from .core.unit import Unit
        from .generators.shareable_clip import validate_draft

        if self.sha256 != self.digest(self.model_dump(mode="json")):
            raise ValueError("Prepared story checksum mismatch")
        expected = "full-story-v2" if self.schema_version == 2 else "shareable-clip-v1"
        if self.validator_version != expected:
            raise ValueError("Mismatched script validator")
        if self.schema_version == 2:
            from .prepared_rundown import validate_sections

            if validate_sections(self.draft, self.inputs, self.background):
                raise ValueError("Invalid selected opening/closing")
        elif "opening" in self.draft or "closing" in self.draft:
            raise ValueError("Legacy clips cannot contain unchecked rundown")
        card = PersonaCard.from_dict(self.inputs.persona_card)
        issues = validate_draft(
            self.draft,
            {"min_chars": 264, "max_chars": 506},
            {"speech_tics": list(card.speech_tics) if card else []},
        )
        if issues:
            raise ValueError("Prepared story fails original clip constraints")
        if (
            self.show.get("story_core") is not None
            or self.show.get("topic") != self.inputs.topic
        ):
            raise ValueError("Unsupported prepared story structure")
        TypeAdapter(RichPersona).validate_python(self.show["persona"])
        units = TypeAdapter(list[Unit]).validate_python(self.show["units"])
        lines = [
            {"id": line.id, "text": line.text} for unit in units for line in unit.lines
        ]
        if lines != self.draft["lines"] or not 2 <= len(units) <= 4:
            raise ValueError("Prepared show differs from validated draft")
        if any(
            line.cue is not None or line.is_rupture
            for unit in units
            for line in unit.lines
        ):
            raise ValueError("Unsupported prepared line cues")
        return self

    @classmethod
    def capture(cls, inputs, show, trace, *, background):
        if trace.get("status") != "completed" or not inputs.language.startswith("zh"):
            raise ValueError("Only fully validated Chinese clips can be saved")
        if (
            trace.get("budget", {}).get("min_chars") != 264
            or trace.get("budget", {}).get("max_chars") != 506
        ):
            raise ValueError("Unsupported clip budget; no relaxed import")
        data = {
            "schema_version": 1,
            "validator_version": "shareable-clip-v1",
            "inputs": inputs.model_dump(mode="json"),
            "background": background,
            "show": json.loads(json.dumps(asdict(show))),
            "draft": trace["final_draft"],
        }
        return cls.model_validate({**data, "sha256": cls.digest(data)})


def generate_story(inputs, config, options: SessionOptions, *, engine=None):
    """Generate a strictly validated durable clip without opening audio or a room."""
    from .live.engine import EchuuLiveEngine
    from .modes.topic_brief import merge_brief_into_background, produce_topic_brief

    if config.story_pipeline != "legacy_v4" or not inputs.language.startswith("zh"):
        raise ValueError("Prepared stories require the Chinese legacy clip pipeline")
    engine = engine or EchuuLiveEngine(config=config)
    brief = produce_topic_brief(engine.llm, inputs.topic) if options.grounding else ""
    background = f"【本次用户设定·故事前提】{inputs.background}\n本次主题：{inputs.topic}\n保留用户人设与明确虚构设定；现实产品和新闻若与最新有来源的事实冲突，应更正旧背景，不得默认把真实事件当成虚构。"
    if inputs.memory:
        background += f"\n【历史记忆·低于本次设定，不能作为本次新经历】{inputs.memory}"
    background = merge_brief_into_background(background, inputs.topic, brief)
    engine.setup(
        name=inputs.name,
        persona=inputs.persona,
        topic=inputs.topic,
        background=background,
        language=inputs.language,
        persona_card=inputs.persona_card,
        character_config={"output_format": "shareable_clip", "target_seconds": 90},
    )
    body = PreparedStory.capture(
        inputs, engine.state.show, engine._legacy_writer_trace, background=background
    )
    from .prepared_rundown import generate_sections

    sections = generate_sections(engine, inputs, background, body.draft)
    data = body.model_dump(mode="json")
    data.update(schema_version=2, validator_version="full-story-v2")
    data["draft"].update(sections)
    return PreparedStory.model_validate({**data, "sha256": PreparedStory.digest(data)})


def load_story(engine, artifact: PreparedStory):
    """Restore fresh per-play state; no model call and no shared mutable script."""
    from .core.persona_card import PersonaCard
    from .core.persona_model import RichPersona
    from .core.unit import Show, Unit
    from .live.language import setup_stream_language_from_topic
    from .live.performer import PerformerV3
    from .live.state import PerformanceState, PerformerMemory

    artifact = PreparedStory.model_validate(artifact.model_dump(mode="json"))
    source = artifact.inputs
    units = TypeAdapter(list[Unit]).validate_python(artifact.show["units"])
    persona = TypeAdapter(RichPersona).validate_python(artifact.show["persona"])
    show = Show(persona=persona, topic=source.topic, units=units)
    lines = [line for unit in units for line in unit.lines]
    memory = PerformerMemory()
    memory.script_progress.update(total_lines=len(lines), current_stage=lines[0].stage)
    engine.state = PerformanceState(
        name=source.name,
        persona=source.persona,
        background=artifact.background,
        topic=source.topic,
        show=show,
        script_lines=lines,
        memory=memory,
    )
    engine.persona_card = PersonaCard.from_dict(source.persona_card)
    engine.stream_lang_context = setup_stream_language_from_topic(
        source.topic, source.persona
    )
    engine._source_material = {
        "name": source.name,
        "persona": source.persona,
        "topic": source.topic,
        "background": artifact.background,
    }
    if hasattr(engine, "llm"):
        engine.performer = PerformerV3(
            engine.llm,
            engine.tts,
            engine.danmaku_handler,
            stream_lang_context=engine.stream_lang_context,
        )
    return engine.state
