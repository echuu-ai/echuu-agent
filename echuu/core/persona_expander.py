"""PersonaExpander — 从用户设定扩充一份 CharacterDossier。

四角度机械推导（代价/反面/谎言/未了），只补【一个】未解决的坎、不补解决方案，
冲突必须长在用户已写的内容上。单次 LLM 调用 + retry once + 退化兜底（绝不崩）。

See docs/superpowers/specs/2026-07-25-persona-dossier-content-quality-design.md §1.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Protocol

from echuu.core.persona_card import PersonaCard
from echuu.core.persona_dossier import BehaviorRules, CharacterDossier, Conflict
from echuu.core.prompt_leakage import find_prompt_leaks
from echuu.core.tuning_guidance import render_generation_constraints
from echuu.diagnostics import diagnostic


class _LLM(Protocol):
    def generate(self, prompt: str) -> str: ...


PROMPT_VERSION = "persona-expander-v3-grounded-hypothesis"


_PROMPT = """\
你是直播角色的戏剧顾问。读完这个角色的设定，给它补上一道【过不去的坎】。
原则（最重要）：
- 设定回答的是这个人是谁（静态，只能被朗读）；冲突回答的是他现在卡在哪（会逼出选择=故事）。
- 冲突必须【长在用户已写的东西上】：取用户给的最强特质，问它的代价。绝不引入外来元素，
  不能让用户觉得这不是自己的角色。
- 只补【一个】未解决的坎，不补解决方案——完整的角色（有困境也有和解）没有故事可讲。
- 这是人物假设，不是替用户编履历。不得补造日期、数量、身体特征、伤病、私人物件、历史事件、
  他人原话或已经发生过的具体场景。输入没给的事实一律不写。
- 具体感来自当下可表演的一般动作（例如停顿、改口、回避问题），不是疤痕、病史、截图或精确次数。

四个可套的角度，选**最贴合这个角色最强特质**的一个。
每个角度都要从【用户写的那条特质本身】推，不要套用通用人格模板：
- 代价：为了成为现在这样，付出或失去了什么
- 反面：这个优点在什么情况下反过来变成伤害
- 谎言：他相信的某件事其实站不住，但他还没发现
- 未了：欠着谁一件没还的事，或有句话一直没说出口

角色名：{name}
人设：{persona}
背景：{background}
{card_block}
严格输出 JSON（不要任何额外文字、不要 markdown 围栏）：
{{
  "expanded_bio": "<只用输入已有事实扩成2-3句，并明确为当前状态；不得新增人物履历>",
  "conflict": {{
    "angle": "<代价|反面|谎言|未了 之一>",
    "premise": "<你选中的那个最强特质，照抄用户原话>",
    "statement": "<冲突一句话：这个特质如何在他身上变成一道没解决的坎>"
  }},
  "behavior_rules": {{
    "avoid_topics": ["<由输入特质自然推导的抽象话题，1-2个；不得暗示新经历>"],
    "trigger": "<被X → 先Y，然后Z 的即时反应模式>",
    "contradiction": "<言行不一：嘴上A，行为B。这是全场最有用的一条>",
    "breaking_point": "<临界点：被反复触碰后突然爆发的那一下，会成为高潮>"
  }}
}}
"""


def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    return m.group(1) if m else raw


def _parse(raw: str) -> Optional[dict[str, Any]]:
    try:
        obj = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _fallback(persona: str) -> CharacterDossier:
    """LLM 不可用时的最小兜底：从 persona 首句造一条'反面'冲突，statement 永不为空。"""
    stripped = (persona or "").strip()
    trait = (stripped.splitlines()[0][:30] if stripped else "") or "这个人"
    return CharacterDossier(
        expanded_bio=trait,
        conflict=Conflict(angle="反面", premise=trait,
                          statement=f"{trait}——但正是这一点在悄悄消耗 ta"),
        behavior_rules=BehaviorRules(
            avoid_topics=("承认自己也会累",),
            trigger="被戳到那点 → 先笑着带过，然后沉默一下",
            contradiction="嘴上说没事，行为在回避",
            breaking_point="被反复追问时会突然认真，然后立刻打岔",
        ),
    )


class PersonaExpander:
    def expand(self, name: str, persona: str, background: str,
               card: "PersonaCard | None", llm: _LLM,
               tuning_guidance=None) -> CharacterDossier:
        card_block = ""
        if card and not card.is_empty():
            card_block = ("【用户人设卡（硬约束，冲突要与之一致）】\n"
                          f"{card.render_for_prompt()}\n")
        prompt = _PROMPT.format(
            name=name or "", persona=persona or "", background=background or "",
            card_block=card_block,
        )
        quality_flags = render_generation_constraints(tuning_guidance, "dossier")
        if quality_flags:
            prompt += (
                "\n\n【静默质量标志】\n"
                f"{quality_flags}\n"
                "只在内部检查，不得在输出中提及标志名、规则或调整过程。"
            )
        for _ in range(2):  # one retry
            try:
                raw = llm.generate(prompt)
            except Exception as exc:  # noqa: BLE001 — 扩充失败不阻塞开播
                diagnostic(f"[PersonaExpander] LLM 调用失败: {exc}")
                continue
            obj = _parse(raw)
            if not obj:
                continue
            if find_prompt_leaks(obj, tuning_guidance):
                continue
            dossier = CharacterDossier.from_dict(obj)
            if dossier is not None and dossier.conflict.statement:
                return dossier
        return _fallback(persona)
