import json
from time import perf_counter

from .core.output_safety import sanitize_audience_text
from .generators.legacy_v4 import issues_for, parse_object

EMOTIONS = {
    "opening": {"joy", "excited", "surprised", "shy"},
    "closing": {"joy", "relaxed", "shy"},
}


class RundownValidationError(ValueError):
    def __init__(self, issues):
        self.details = {"issues": issues}
        super().__init__("Full script opening/closing validation failed")


def validate_sections(draft, inputs, background):
    """Validate final saved words; never sanitize, truncate or omit invalid text."""
    source = {
        "name": inputs.name,
        "persona": inputs.persona,
        "topic": inputs.topic,
        "background": background,
    }
    issues = []
    tics = (inputs.persona_card or {}).get("speech_tics", [])
    for section, lower, upper, prefix in (
        ("opening", 2, 3, "O"),
        ("closing", 1, 2, "C"),
    ):
        lines = draft.get(section)
        if not isinstance(lines, list) or not lower <= len(lines) <= upper:
            issues.append({"section": section, "code": "section_count"})
            continue
        for index, line in enumerate(lines):
            if not isinstance(line, dict) or set(line) != {"id", "text", "emotion"}:
                issues.append({"section": section, "code": "format"})
                continue
            text = line.get("text")
            if (
                line.get("id") != f"{prefix}{index + 1:03d}"
                or line.get("emotion") not in EMOTIONS[section]
            ):
                issues.append({"section": section, "code": "format"})
            if (
                not isinstance(text, str)
                or text != text.strip()
                or not 10 <= len(text) <= 25
            ):
                issues.append({"section": section, "code": "sentence_bounds"})
                continue
            result = sanitize_audience_text(text, source_material=source)
            if result.issues or result.text != text:
                issues.append({"section": section, "code": "content_guard"})
            issues.extend(
                {"section": section, "code": item["code"]}
                for item in issues_for([line], {"speech_tics": tics})
            )
    return issues


def generate_sections(engine, inputs, background, draft):
    """Generate the selected opening/closing before any room or TTS is opened."""
    schema = {
        "type": "object",
        "properties": {},
        "required": ["opening", "closing"],
        "additionalProperties": False,
    }
    for section, low, high, prefix in (("opening", 2, 3, "O"), ("closing", 1, 2, "C")):
        schema["properties"][section] = {
            "type": "array",
            "minItems": low,
            "maxItems": high,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": f"^{prefix}[0-9]{{3}}$"},
                    "text": {"type": "string", "minLength": 10, "maxLength": 25},
                    "emotion": {"type": "string", "enum": sorted(EMOTIONS[section])},
                },
                "required": ["id", "text", "emotion"],
                "additionalProperties": False,
            },
        }
    prompt = """为已通过校验的正文补齐开场和收尾，正文不得重写。返回 opening、closing 两个数组的 JSON 对象。
开场2至3句，ID依次O001、O002、O003；先自然打招呼，再过渡到正文，不自报身份、不念主题或节目预告。
收尾1至2句，ID依次C001、C002；简短收束既定正文并道别，不升华，不致谢陪伴，不依赖直播中是否收到礼物，不新增事件或经历。
每句严格10至25个字符（含标点），口语，无括号动作和内部指令。每行只有id、text、emotion三个字段。
开场情绪joy/excited/surprised/shy；收尾joy/relaxed/shy。
保留用户人设和背景，口癖仅用配置中的词；不编造已收到的礼物、弹幕或亲身经历；现实事实只引用给定背景。
DATA:\n""" + json.dumps(
        {
            "input": inputs.model_dump(mode="json"),
            "background": background,
            "body": draft["lines"],
        },
        ensure_ascii=False,
    )
    trace = []
    engine._prepared_rundown_trace = trace
    for attempt in range(3):
        started = perf_counter()
        try:
            caller = getattr(engine.llm, "call_structured", None)
            raw = (
                caller(prompt, response_schema=schema, max_tokens=900)
                if callable(caller)
                else engine.llm.call(prompt, max_tokens=900)
            )
            obj = parse_object(raw)
            problems = validate_sections(obj, inputs, background)
            if set(obj) != {"opening", "closing"}:
                problems.append({"section": "opening", "code": "format"})
        except (ValueError, TypeError, KeyError, AttributeError):
            obj, problems = {}, [{"section": "opening", "code": "format"}]
        trace.append(
            {
                "round": attempt + 1,
                "seconds": round(perf_counter() - started, 3),
                "issues": problems,
            }
        )
        if not problems:
            return obj
        prompt += (
            "\n修复上一轮，不放宽句数、长度或内容约束。重新返回完整opening和closing。\n"
            + json.dumps({"previous": obj, "issues": problems}, ensure_ascii=False)
        )
    raise RundownValidationError(problems)


def render_line(line, stage, tts):
    """Render exactly the saved line, rejecting missing audio instead of skipping."""
    from .modes.reaction import _cls_instruction

    audio = None
    if tts.enabled:
        tts.set_instruction(_cls_instruction("NATURAL_CASUAL", line["emotion"]))
        for attempt in range(2):
            try:
                audio = tts.synthesize(line["text"])
                if audio:
                    break
            except Exception:
                if attempt:
                    raise
        if not audio:
            raise ValueError(f"Selected {stage} audio unavailable")
    return {
        "type": "step",
        "mode": "storytelling",
        "event": stage,
        "stage": stage,
        "line": {"id": line["id"]},
        "speech": line["text"],
        "emotion": line["emotion"],
        "cls": "NATURAL_CASUAL",
        "motion": {"state": "greet" if stage == "opening" else "farewell"},
        "audio": audio,
    }
