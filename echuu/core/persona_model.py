"""PersonaModel — identity / belief / flaw axes + visual anchor + title hook.

Locked after one LLM extraction; never mutated mid-show.
See docs/superpowers/specs/2026-05-30-echuu-rupture-engine-design.md §2.1.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from echuu.diagnostics import diagnostic


class _LLM(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class RichPersona:
    """V2 人设锚——在三轴之外补足「好讲故事」所需的活人感素材。

    由 PersonaAnalyst 从用户设定自动推导；一次抽完锁定全程不变。
    See docs/superpowers/specs/2026-05-31-rupture-engine-content-quality-design.md §3.1.
    """
    identity: str
    belief: str
    flaw: str                                  # I4: 永不为空
    verbal_tics: tuple[str, ...] = field(default_factory=tuple)            # 语癖
    situational_weaknesses: tuple[str, ...] = field(default_factory=tuple) # 情境弱点
    contrast_hook: str = ""                     # 反差/悬疑
    life_anchors: tuple[str, ...] = field(default_factory=tuple)          # 生活化具体物品/场景
    title_hook: str = ""
    visual_anchor: str = ""


_PROMPT_TEMPLATE = """\
你在帮一个 5 分钟单人直播节目准备人设锚。
读完这段 persona 描述，输出严格 JSON（不要任何额外文字）：

{{
  "identity": "<观众秒认的身份，一句话>",
  "belief": "<中段会被外露的信念，一句话>",
  "flaw": "<末段会闪现的弱点/反常点，一句话；越具体越好>",
  "visual_anchor": "<贯穿性视觉锚，如配饰/小动作/背景物>",
  "title_hook": "<像新闻标题，能让中途刷到的人不划走，<= 30 字>"
}}

Persona 描述：
{persona}
"""


def _placeholder_from(persona: str) -> dict[str, str]:
    """Fallback values when LLM fails — flaw must NEVER be None (I4)."""
    head = (persona or "未提供 persona").strip().splitlines()[0][:40]
    return {
        "identity": head or "[待补充身份]",
        "belief": "[待补充信念]",
        "flaw": "[待补充弱点]",
        "visual_anchor": "[待补充视觉锚]",
        "title_hook": "[待补充开场钩]",
    }


def _parse_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    # Strip ```json fences if present
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


@dataclass(frozen=True)
class PersonaModel:
    identity: str
    belief: str
    flaw: str
    visual_anchor: str
    title_hook: str

    @classmethod
    def from_persona_text(cls, persona: str, llm: _LLM) -> "PersonaModel":
        prompt = _PROMPT_TEMPLATE.format(persona=persona)
        for attempt in range(2):  # one retry
            try:
                raw = llm.generate(prompt)
            except Exception as exc:  # LLM outage / API error → degrade, don't crash (spec §5)
                diagnostic(f"[PersonaModel] LLM 调用失败 (attempt {attempt + 1}): {exc}")
                continue
            obj = _parse_json(raw)
            if obj and all(k in obj and isinstance(obj[k], str) and obj[k].strip()
                           for k in ("identity", "belief", "flaw", "visual_anchor", "title_hook")):
                return cls(
                    identity=obj["identity"],
                    belief=obj["belief"],
                    flaw=obj["flaw"],
                    visual_anchor=obj["visual_anchor"],
                    title_hook=obj["title_hook"],
                )
        # Degraded path — flaw MUST be non-None (I4)
        ph = _placeholder_from(persona)
        return cls(
            identity=ph["identity"],
            belief=ph["belief"],
            flaw=ph["flaw"],
            visual_anchor=ph["visual_anchor"],
            title_hook=ph["title_hook"],
        )
