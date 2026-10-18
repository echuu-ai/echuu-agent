import json

import pytest

from echuu.generators.legacy_v4 import ScriptGeneratorV4
from echuu.generators.shareable_clip import validate_draft


def plan():
    return {
        "candidates": [{
            "id": "a", "premise": "联调时把预览误当开播", "expectation": "检查完成",
            "surprise": "发现还没连接房间", "resolution": "按步骤核对",
            "character_choice": "直接解释状态", "share_reason": "容易遇到的调试误会",
        }],
        "selected_id": "a",
        "voice_design": {"provenance": "按本次联调助手人设设计"},
    }


def draft(short=False, beats=7):
    texts = [
        "今天检查创建场次时，我先看了数据库里的状态。记录已经生成，但还没连接房间，旁边的同事却以为画面已经发给观众了。",
        "我请他看看连接提示，他笑着说自己刚才只顾着检查按钮颜色。我们一起打开房间状态，确认浏览器还没有拿到连接凭据。",
        "连接完成后，我们又检查了音轨和视频轨。他问能不能先省掉画面，我说当然可以，先把语音和消息这两条路验证清楚。",
        "接下来轮到调度助手。我看到请求已经接受，就继续等它真正入房；同事这次学乖了，主动提醒我接受请求还不等于开始播放。",
        "我们在预览里送了一份礼物，先记录收到的批次，再核对播放开始和结束。同事终于把感谢字幕和真正播出的声音区分开了。",
        "最后检查完成回执时，我特意把同一条请求重试了一次。数据库里的已播计数没有增加，这回大家都知道该看哪个记录了。",
        "收尾时我们结束测试场次，等任务和监听都清理完。同事说原来今天最重要的进展，就是终于愿意先看状态，再宣布成功。",
    ]
    if short:
        texts = [text[:36] for text in texts]
        texts[0] = texts[0][:-1]
    return {
        "lines": [{"id": f"L{i+1:03d}", "text": text} for i, text in enumerate(texts[:beats])],
        "quotables": [{"line_id": "L001", "quote": "先看了数据库里的状态", "why_shareable": "先核实再判断"}],
    }


class Writer:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.prompts = []

    def call(self, prompt, **kwargs):
        self.prompts.append(prompt)
        value = next(self.outputs)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def generate(outputs):
    writer = Writer([plan(), *outputs])
    generator = ScriptGeneratorV4(writer)
    lines = generator.generate(
        "联调助手", "用简短中文说明当前正在验证的直播步骤", "", "创建场次和连接房间",
        character_config={"output_format": "shareable_clip"},
    )
    return lines, writer, generator.last_trace


@pytest.mark.parametrize("invalid", [draft(beats=4), {"lines": ["invalid"] * 7}, "not JSON"])
def test_invalid_structure_is_repaired_before_any_script_is_returned(invalid):
    lines, writer, trace = generate([invalid, draft()])
    assert len(lines) == 7
    assert len(writer.prompts) == 3
    assert trace["status"] == "completed"
    assert not validate_draft({"lines": [{"id": line.id, "text": line.text} for line in lines], "quotables": draft()["quotables"]}, trace["budget"], {})


def test_second_repair_receives_the_remaining_character_deficit():
    lines, writer, trace = generate([draft(short=True), draft(short=True), draft()])
    assert len(lines) == 7
    assert len(writer.prompts) == 4
    assert "min_chars" in writer.prompts[-1]
    assert "missing_chars" in writer.prompts[-1]
    assert trace["draft_attempts"][0]["chars"] == 251
    assert trace["budget"]["min_chars"] <= sum(len(line.text) for line in lines) <= trace["budget"]["max_chars"]


def test_exhausted_repair_never_returns_an_invalid_clip():
    writer = Writer([plan(), draft(short=True), draft(short=True), draft(short=True)])
    generator = ScriptGeneratorV4(writer)
    with pytest.raises(ValueError, match="clip constraints unresolved"):
        generator.generate("联调助手", "简短中文", "", "创建场次", character_config={"output_format": "shareable_clip"})
    assert len(writer.prompts) == 4
    assert generator.last_trace["status"] == "needs_review"


def test_direct_validation_stays_strict():
    with pytest.raises(ValueError, match="5..10"):
        validate_draft(draft(beats=4), {"min_chars": 264, "max_chars": 506}, {})
    assert any(issue["code"] == "duration" for issue in validate_draft(draft(short=True), {"min_chars": 264, "max_chars": 506}, {}))
