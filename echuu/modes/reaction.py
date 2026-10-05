"""Reaction 模式预生产管线。

源视频（公网 URL）
  → qwen3.5-omni 时间戳标注（画面+语音+音效一体理解）
  → 反应脚本（persona 化，注入误判/过度反应/走神等不完美性）
  → TTS（instruct 情绪指令）
  → step 事件列表（event=react，sync.video_t 对齐源视频时钟）
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable, List, Optional

from echuu.diagnostics import diagnostic

from ..live.motion import pick_motion_for_reaction
from ..live.timeline import estimate_wav_duration

# 反应太迟就丢弃（秒）：宁可漏反应，不要错位反应
MAX_REACTION_DELAY = 4.0
# 相邻反应之间的最小呼吸间隔（秒）
REACTION_GAP = 0.4

_ANNOTATE_PROMPT = """请分析这个视频，输出带时间戳的事件列表，用于给一个虚拟主播做 reaction 视频。

要求输出 JSON 数组（不要 markdown 代码块），每个元素：
{"t": 秒数(float), "kind": "visual|speech|sound|highlight", "desc": "这一刻发生了什么（一句话）", "transcript": "如有人说话/唱歌给出原文，否则空字符串"}

规则：
- 8 到 15 个事件，覆盖全片；highlight 标记最值得反应的 2-4 个时刻
- t 必须是事件真实发生的时间点
- 只输出 JSON 数组本身"""

_SCRIPT_PROMPT = """你是虚拟主播「{name}」。人设：{persona}
背景：{background}

你正在直播看一个 {video_duration:.0f} 秒的视频，边看边实时反应。下面是视频的时间轴事件：

{events_json}

为你自己写反应台词。输出 JSON 数组（不要 markdown 代码块），每个元素：
{{"video_t": 反应触发的视频时间点(float), "text": "<台词，口语，{text_len}>", "emotion": "joy|excited|surprised|frustrated|sad|smug|shy 之一", "cls": "NATURAL_CASUAL|THEATRICAL|HIGH_LOAD|HYPER_FLUENT 之一"}}

铁律（这是节目好看的关键）：
1. 只写 {n_reactions} 条反应，挑最有戏的时刻——错过一两个精彩瞬间反而真实
2. 至少 1 条是误解：你理解错了画面内容，还一本正经地分析
3. 至少 1 条是过度反应：对一个平平无奇的细节反应过大（THEATRICAL）
4. 开头第一条在 video_t=0 附近，是随意的开场白（NATURAL_CASUAL）
5. 全程保持「{name}」的说话习惯和口癖，禁止出现主播身份之外的视角
6. 相邻反应的 video_t 间隔至少 {min_gap} 秒，台词必须在下一条之前说得完——中文语速约每秒 4 个字，自己算好

只输出 JSON 数组本身。"""


def _parse_json_array(text: str) -> list:
    """LLM 输出 → JSON 数组（容忍 markdown 代码块包裹）。"""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*?])\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中找不到 JSON 数组: {text[:200]}")
    return json.loads(text[start:end + 1])


def annotate_video(video_url: str, model: Optional[str] = None) -> list:
    """qwen3.5-omni 对视频做时间戳标注，返回事件列表。"""
    import dashscope

    model = model or os.getenv("REACTION_OMNI_MODEL", "qwen3.5-omni-plus")
    messages = [{
        "role": "user",
        "content": [
            {"video": video_url},
            {"text": _ANNOTATE_PROMPT},
        ],
    }]
    # Omni 系列必须流式调用；只要文本模态
    responses = dashscope.MultiModalConversation.call(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        model=model,
        messages=messages,
        modalities=["text"],
        stream=True,
    )
    full_text = ""
    for r in responses:
        try:
            content = r.output.choices[0].message.content
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        full_text += part["text"]
            elif isinstance(content, str):
                full_text += content
        except (AttributeError, IndexError, TypeError):
            continue
    events = _parse_json_array(full_text)
    events.sort(key=lambda e: float(e.get("t", 0)))
    return events


def generate_reaction_script(engine, name: str, persona: str, background: str,
                             video_events: list) -> list:
    """LLM 生成 persona 化反应脚本。反应预算按视频时长动态给。"""
    video_duration = max((float(e.get("t", 0)) for e in video_events), default=30.0) + 5.0
    # 约每 12 秒一条反应，短视频压少、限台词长度
    n_reactions = max(2, min(8, int(video_duration // 12) + 1))
    min_gap = max(6, int(video_duration / (n_reactions + 1)))
    text_len = "10-20字" if video_duration < 60 else "15-40字"

    prompt = _SCRIPT_PROMPT.format(
        name=name,
        persona=persona,
        background=background or "（无）",
        events_json=json.dumps(video_events, ensure_ascii=False, indent=1),
        video_duration=video_duration,
        n_reactions=n_reactions,
        min_gap=min_gap,
        text_len=text_len,
    )
    raw = engine.llm.call(prompt, max_tokens=2000)
    script = _parse_json_array(raw)
    script.sort(key=lambda e: float(e.get("video_t", 0)))
    return script


# CLS → TTS 指令沿用 tts_instruction 的映射
def _cls_instruction(cls: str, emotion: str) -> str:
    from ..live.tts_instruction import _EMOTION_HINTS, CLS_INSTRUCTIONS
    parts = [CLS_INSTRUCTIONS.get(cls, CLS_INSTRUCTIONS["NATURAL_CASUAL"])]
    if emotion in _EMOTION_HINTS:
        parts.append(_EMOTION_HINTS[emotion])
    return "，".join(parts)


def produce_reaction_events(
    engine,
    name: str,
    persona: str,
    background: str,
    topic: str,
    source: dict,
    on_phase: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """预生产：完整产出 reaction step 事件列表（含音频字节）。"""
    video_url = (source or {}).get("video_url")
    if not video_url:
        raise ValueError("reaction 模式需要 source.video_url（公网可访问的视频地址）")

    def phase(msg: str):
        diagnostic(f"[reaction] {msg}")
        if on_phase:
            on_phase(msg)

    phase("Phase A: Omni 视频时间轴标注...")
    video_events = annotate_video(video_url)
    phase(f"标注完成: {len(video_events)} 个事件")

    phase("Phase B: 生成 persona 反应脚本...")
    script = generate_reaction_script(engine, name, persona, background, video_events)
    phase(f"脚本完成: {len(script)} 条反应")

    phase("Phase C: TTS 合成 + 时间对齐...")
    events: List[dict] = []
    cursor = 0.0  # 已占用到的视频时间
    for item in script:
        video_t = float(item.get("video_t", 0))
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        emotion = str(item.get("emotion", "")).lower()
        cls = str(item.get("cls", "NATURAL_CASUAL")).upper()

        # 时间对齐：不能压住上一条；太迟就丢弃
        effective_t = max(video_t, cursor)
        if effective_t - video_t > MAX_REACTION_DELAY:
            phase(f"  丢弃迟到反应 @{video_t:.1f}s: {text[:20]}...")
            continue

        engine.tts.set_instruction(_cls_instruction(cls, emotion))
        audio = None
        for attempt in range(2):  # websocket 偶发超时，重试一次
            try:
                audio = engine.tts.synthesize(text)
                if audio:
                    break
            except Exception as exc:
                diagnostic(f"[reaction] TTS 失败（第{attempt + 1}次）: {exc}")
        duration = estimate_wav_duration(audio)
        cursor = effective_t + duration + REACTION_GAP

        motion = pick_motion_for_reaction(emotion, cls, video_t, text)
        events.append({
            "type": "step",
            "mode": "reaction",
            "event": "react",
            "speech": text,
            "emotion": emotion,
            "cls": cls,
            "motion": motion,
            "intensity": motion.get("intensity", 0.5),
            "sync": {"video_t": video_t},
            "timeline_t": effective_t,
            "audio": audio,
        })

    if not events:
        raise RuntimeError("reaction 预生产失败：没有产出任何有效反应事件")
    phase(f"预生产完成: {len(events)} 个事件")
    return events
