"""Audience-output safety shared by legacy and refactored story pipelines.

The gate is deliberately narrow: it blocks internal prompt text and unsupported
high-risk personal facts, while preserving harmless invented texture that makes
the legacy storyteller sound alive.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .digression_db import scrub_recited_topic
from .prompt_leakage import find_prompt_leaks


_EXACT_NUMBER_RE = re.compile(
    r"\d+(?:\.\d+)?"
    r"(?:秒|分钟|小时|天|周|月|年|次|版|篇|本|份|封|届|岁|个|轮|遍|行|条|句)"
)
_HIGH_RISK_TERMS = (
    "病历号", "住院记录", "诊断证明", "死亡证明", "临终", "复活码",
    "劳务合同", "雇佣合同", "合伙协议", "门禁截图", "聊天记录",
    "伤疤", "疤痕", "旧伤", "病史", "耳垂痘印",
)
_HIGH_RISK_CLAUSE_RE = re.compile(
    r"[^。！？!?\n]{0,50}(?:我妈|妈妈|母亲|我爸|爸爸|父亲|家人)"
    r"[^。！？!?\n]{0,20}(?:去世|离世|没了|走了|走前|走后|走的时候|临终|住院|病房)"
    r"[^。！？!?\n]{0,50}[。！？!?]?"
)
_HIGH_RISK_DOMAIN_CLAUSE_RE = re.compile(
    r"[^。！？!?\n]{0,60}(?:产科病房|护士长|住院记录|病历号|诊断证明|死亡证明|"
    r"劳务合同|雇佣合同|合伙协议)[^。！？!?\n]{0,60}[。！？!?]?"
)
# Defense in depth against the LLM walking back a story premise the user
# actually gave this session (e.g. "already bought X") into it never having
# happened — a photoshopped prop, an old item with a fake label, an official
# source cited to declare the thing doesn't exist, or a quiet purchase
# retraction. Same contract as the high-risk clauses above: only scrubbed
# when the clause is NOT itself grounded in source_material, so a premise
# the user genuinely wrote as "pretended to buy" survives untouched.
_PREMISE_REVERSAL_CLAUSE_RES = (
    re.compile(
        r"[^。！？!?\n]{0,40}(?:P图|PS的|修图软件|合成(?:的)?照片|拼图软件|截图伪造)"
        r"[^。！？!?\n]{0,20}(?:假装|冒充|骗|以为)[^。！？!?\n]{0,40}[。！？!?]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"[^。！？!?\n]{0,20}(?:官网|客服|店员|专柜)[^。！？!?\n]{0,20}(?:查|问了|搜了)"
        r"[^。！？!?\n]{0,20}(?:没有这|不存在|查无|没有卖|还没发售|没有这款|没有这个型号)"
        r"[^。！？!?\n]{0,20}[。！？!?]?"
    ),
    re.compile(
        r"[^。！？!?\n]{0,10}(?:旧|老)[^。！？!?\n]{0,10}(?:手机|设备|机器)"
        r"[^。！？!?\n]{0,20}贴[^。！？!?\n]{0,15}(?:标|图标|logo|贴纸)"
        r"[^。！？!?\n]{0,20}(?:充当|冒充|假装)[^。！？!?\n]{0,20}[。！？!?]?",
        re.IGNORECASE,
    ),
    re.compile(
        r"[^。！？!?\n]{0,20}(?:其实|后来|真相是)[^。！？!?\n]{0,20}"
        r"(?:没有买|没舍得买|退货了|退款了|取消了订单|根本没花钱)[^。！？!?\n]{0,20}[。！？!?]?"
    ),
    re.compile(
        r"[^。！？!?\n]{0,20}(?:奖金|年终奖|工资|薪水|存款|预算)"
        r"[^。！？!?\n]{0,10}(?:其实|后来发现)?(?:是|为)?(?:零|没有|没发|一分没有)"
        r"[^。！？!?\n]{0,20}[。！？!?]?"
    ),
)
_STAGE_DIRECTION_RE = re.compile(
    r"[（(][^）)\n]{0,60}(?:举起|镜头|慢动作|转头|耸肩|轻笑|停顿|切镜|"
    r"提词器|麦克风|耳麦|看镜头|调音量|"
    r"leans?|pauses?|whispers?|microphone|headset)[^）)\n]{0,60}[）)]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SafetyResult:
    text: str
    issues: tuple[str, ...] = ()


def _vague_number(value: str) -> str:
    if any(unit in value for unit in ("秒", "分钟", "小时")):
        return "一会儿"
    if "遍" in value:
        return "几遍"
    if "次" in value:
        return "几次"
    if any(unit in value for unit in ("条", "个", "句")):
        return "一些"
    return "一阵子"


def sanitize_audience_text(
    text: str,
    *,
    source_material: Any = None,
    tuning_guidance: Any = None,
    fallback: str = "刚才脑子打了个结……反正接着说这件事。",
) -> SafetyResult:
    """Return text safe to send to TTS/WebSocket plus machine-readable issues."""
    original = str(text or "").strip()
    if not original:
        return SafetyResult("")
    source_text = json.dumps(source_material or {}, ensure_ascii=False)
    leaks = find_prompt_leaks(original, tuning_guidance)
    if leaks:
        return SafetyResult(fallback, tuple(f"prompt_leak:{item}" for item in leaks))

    issues: list[str] = []
    cleaned = _STAGE_DIRECTION_RE.sub("", original)
    if cleaned != original:
        issues.append("stage_direction")

    def blur_number(match: re.Match[str]) -> str:
        value = match.group(0)
        if value in source_text:
            return value
        issues.append(f"unsupported_number:{value}")
        return _vague_number(value)

    cleaned = _EXACT_NUMBER_RE.sub(blur_number, cleaned)
    topic = ""
    if isinstance(source_material, dict):
        topic = str(source_material.get("topic") or "")
    scrubbed = scrub_recited_topic(cleaned, topic)
    if scrubbed != cleaned:
        issues.append("topic_recital_or_stock_aside")
        cleaned = scrubbed
    for term in _HIGH_RISK_TERMS:
        if term in cleaned and term not in source_text:
            issues.append(f"unsupported_fact:{term}")
            cleaned = cleaned.replace(term, "那件没法确认的事")
    for match in tuple(_HIGH_RISK_CLAUSE_RE.finditer(cleaned)):
        clause = match.group(0)
        if clause and clause not in source_text:
            issues.append("unsupported_fact:family_death")
            cleaned = cleaned.replace(clause, "有些太沉的细节我不乱补，先说回眼前这件事。")
    for match in tuple(_HIGH_RISK_DOMAIN_CLAUSE_RE.finditer(cleaned)):
        clause = match.group(0)
        if clause and clause not in source_text:
            issues.append("unsupported_fact:high_risk_domain")
            cleaned = cleaned.replace(clause, "这段细节没有可靠依据，我不乱补。")
    for pattern in _PREMISE_REVERSAL_CLAUSE_RES:
        for match in tuple(pattern.finditer(cleaned)):
            clause = match.group(0)
            if clause and clause not in source_text:
                issues.append("premise_reversal")
                cleaned = cleaned.replace(
                    clause, "这件事就是这次真实发生的，我不会说着说着就推翻它。"
                )
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return SafetyResult(cleaned or fallback, tuple(dict.fromkeys(issues)))
