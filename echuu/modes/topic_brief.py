"""主题语义 grounding — 开播前联网检索主题的真实含义。

治「云养猫」类语义误解：网络流行语的实际用法常与字面不符
（云养猫 = 自己不养、刷猫视频/持续追踪猫博主，不是"云端养猫"）。
仅当 LLM provider 支持联网（QwenClient.call_with_search / DashScope enable_search）
时生效；不支持或检索失败一律降级为空字符串，不阻塞开播。
"""
from __future__ import annotations

_BRIEF_PROMPT = """搜索并解释「{topic}」这个话题在当前中文互联网语境下的真实含义。要求：
- 先给出它的实际用法定义（警惕字面意思与网络实际用法不一致，例如"云养猫"实指自己不养猫、靠刷猫视频/追猫博主过瘾）
- 再列 2-3 个典型场景或相关梗
- 100 字以内，纯文本，不要标题和列表符号"""


def produce_topic_brief(llm, topic: str) -> str:
    """联网检索主题简报；provider 不支持或失败返回空串。"""
    if not topic:
        return ""
    search = getattr(llm, "call_with_search", None)
    if not callable(search):
        return ""
    try:
        text = search(_BRIEF_PROMPT.format(topic=topic), max_tokens=400)
    except Exception as exc:  # noqa: BLE001 — grounding 失败不阻塞开播
        print(f"[topic-brief] 联网检索失败（跳过）: {exc}")
        return ""
    return (text or "").strip()


def merge_brief_into_background(background: str, topic: str, brief: str) -> str:
    """把主题简报作为背景资料段拼进 background；空 brief 原样返回。"""
    if not brief:
        return background
    section = f"【主题参考·联网检索，不是主播经历】「{topic}」：{brief}\n以上仅供理解用语，不得据此否定或替换用户本次明确给定的故事前提，也不得把检索中的人物经历当作主播自己的经历。"
    return f"{background}\n\n{section}" if background else section
