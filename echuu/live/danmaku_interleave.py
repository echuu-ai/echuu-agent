"""直播断句处的简短互动回应；后续剧情由 StorySteerer 单独连续改写。"""
from __future__ import annotations

from typing import Protocol
import json

from echuu.live.untrusted import render_untrusted, sanitize_untrusted


class _LLM(Protocol):
    def generate(self, prompt: str) -> str: ...


_PROMPT = """\
你在演一个正在直播讲故事的主播，此刻有条弹幕飘过，你要【插一句话】回应观众，然后自然接到当前话题的后续内容。

你是谁：{identity}
你的语癖（用一个）：{verbal_tics}
你接下来正在讲的事：{topic}
本次处理决定与近期已播内容：{evolution}
你刚说到：{last_line}

弹幕来自观众「{user}」：
{danmaku}

要求：
- 只回一句，15到30字，最多40字。自然口语，不强塞口癖。
- 根据处理决定，简单回应、补充当前话题或接向相关新话题；如果仅回应，不要承诺后面展开。
- 已进入新话题时不要说“先回原来的故事”，不要否认前面已经说过的事情。
- 礼物按真实名称和类别理解，饭团是食物，不是石头；不要念旧资源ID。
- 不要强行说回原稿，不复述之前的台词，不添加产品事实。
- 只输出你要说的话，不要任何解释、不要引号、不要括号说明。
{card_rules}"""

_QUIP_PROMPT = """\
你在演一个正在直播的主播。你刚向观众抛了一个问题，结果弹幕没人理你。
自嘲一句（1 句，带你的语癖，别卖惨），然后半句话接回你正在讲的事。

你是谁：{identity}
你的语癖（用一个）：{verbal_tics}
你刚问的：{question}
你正在讲的事：{topic}

只输出你要说的话，不要任何解释、不要引号、不要括号说明。
"""


class DanmakuInterleaver:
    def __init__(self, llm: _LLM) -> None:
        self.llm = llm

    def respond(self, *, identity: str, verbal_tics, topic: str,
                last_line: str, danmaku_text: str, user: str,
                card=None, gags=None, evolution=None) -> str | None:
        tics = "、".join(verbal_tics) if verbal_tics else "（自然口语）"
        # 人设卡加成：固定称呼；雷点被戳 → 破防加倍；骄傲点被夸/被质疑 → 反应加倍；顺手挂已埋的梗
        card_rules = ""
        if card is not None:
            rules = []
            if getattr(card, "audience_nickname", ""):
                rules.append(f"- 提到观众整体时用固定称呼「{card.audience_nickname}」。")
            has_internals = False
            if getattr(card, "fears", ()):
                has_internals = True
                rules.append(f"- 若弹幕戳中你的雷点（{'；'.join(card.fears)}），反应要明显破防/炸毛，加倍！")
            if getattr(card, "prides", ()):
                has_internals = True
                rules.append(f"- 若弹幕夸到/质疑你的骄傲点（{'；'.join(card.prides)}），要明显得意/不服，加倍！")
            if has_internals:
                # 雷点/骄傲点是内部触发器：只决定反应强度，复述出来就是当众念设定表
                rules.append("- 上面这些雷点/骄傲点只用来决定你的反应强度，不要把它们念出来。")
            if rules:
                card_rules += "\n".join(rules) + "\n"
        if gags:
            card_rules += f"- 如果能自然接上你前面埋的梗（{'；'.join(gags)}），优先顺手接一下。\n"
        prompt = _PROMPT.format(
            identity=identity or "一个主播",
            verbal_tics=tics,
            topic=topic or "（正在讲的事）",
            last_line=last_line or "",
            # 观众可控字段：用户名和弹幕正文都要去结构、限长
            user=sanitize_untrusted(user or "观众", max_chars=24),
            danmaku=render_untrusted(danmaku_text or ""),
            card_rules=card_rules,
            evolution=json.dumps(evolution or {}, ensure_ascii=False),
        )
        try:
            reply = self.llm.generate(prompt)
        except Exception as exc:
            print(f"[DanmakuInterleaver] LLM 调用失败，跳过本次穿插: {exc}")
            return None
        reply = (reply or "").strip().strip('"“”')
        if len(reply) > 40:
            return None
        return reply or None

    def no_reaction_quip(self, *, identity: str, verbal_tics, topic: str,
                         question: str) -> str | None:
        """互动点没人理时的自嘲兜底（不写死台词，人设化生成）。"""
        tics = "、".join(verbal_tics) if verbal_tics else "（自然口语）"
        prompt = _QUIP_PROMPT.format(
            identity=identity or "一个主播",
            verbal_tics=tics,
            question=question or "",
            topic=topic or "（正在讲的事）",
        )
        try:
            reply = self.llm.generate(prompt)
        except Exception as exc:
            print(f"[DanmakuInterleaver] 自嘲兜底生成失败，跳过: {exc}")
            return None
        reply = (reply or "").strip().strip('"“”')
        return reply or None
