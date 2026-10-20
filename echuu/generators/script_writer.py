"""ScriptWriter (agent②) — 从故事内核创作 5 分钟直播台词。

目标不是短视频文案，而是真实直播切片的质感：90% 可信的松弛铺垫托起 1 个
高光时刻。允许自然口语，限制信息和修辞预算，并让每一段都推进同一个中心结论。
范例密度以 data/ 下真实切片为准。

不是「按 beat 填格子」——它拿 RichPersona + StoryCore + few-shot 真实片段，通盘规划
内容结构并把它连贯地落进 2-4 个时间/音色桶。

单次 LLM 调用 + 退化兜底。
See docs/superpowers/specs/2026-05-31-rupture-engine-content-quality-design.md §4.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Protocol

from echuu.core.discourse_mode import MODE_GUIDES, MODE_LABELS
from echuu.core.persona_dossier import CharacterDossier
from echuu.core.persona_model import RichPersona
from echuu.core.prompt_leakage import find_prompt_leaks
from echuu.core.story_core import StoryCore
from echuu.core.tuning_guidance import render_generation_constraints
from echuu.core.unit import ScriptLine, Unit
from echuu.diagnostics import diagnostic


class _LLM(Protocol):
    def generate(self, prompt: str) -> str: ...


PROMPT_VERSION = "script-writer-v6-compact-anti-leak"


_PROMPT_TEMPLATE = """\
你是直播主播的影子写手。写一段 2.5-5 分钟、自然口语化的直播台词。
全篇只回答一个内容主轴：普通铺垫占大多数，只设 1 个高光；事不用有趣，看法要有趣，
但事实不能加戏，也不要强造认知翻转。

【唯一事实来源】
可当作事实的原始输入：
{source_material}
用户人设卡：
{persona_card}
只有以上两块可以支持事实。下方人物分析、内容内核和 Dossier 只指导口吻与结构，
不得据此新增日期、数量、身体特征、伤病、物件、人物往事、他人原话或观众反应。

【人物口吻】
身份={identity}
信念={belief}
弱点={flaw}
语癖={verbal_tics}
情境弱点={situational_weaknesses}
反差={contrast_hook}
有来源的生活锚点={life_anchors}

【本轮内容合同】
话题={topic}
类型={discourse_mode_label}（{discourse_mode}）
类型理由={mode_rationale}
类型结构={mode_guide}
语气={tone_mode}
深度={depth_budget}
娱乐机制={entertainment_mechanism}
唯一主轴={spine}
核心判断点={core_struggle}
支撑点（最多两个）={supporting_points}
推进/payoff={twist}
核心反差={central_contrast}
情绪暗线={emotional_undercurrent}
洞察种子={punchline_seeds}
开场角度={hook_angle}

【节奏分段】
{unit_rails}

【真实切片参考】
只学自然程度、密度与推进方式，不抄事实和措辞：
{examples}

【单核心写作契约】
1. 每段只能建立主轴、提供依据、给出判断/边界或收束主轴；purpose 与 spine_link 只写进 JSON 字段，
   绝不能说进 lines.text。删掉不影响主轴的段落，不另开支线。
2. 全场最多一个组织框架/比喻、一个高光、1-2 个有来源的生活锚点；每行最多引入 1 个新信息。
3. 允许停顿、改口、碎碎念和一次短回环；重复必须增加信息。开场直接接住观众话头并自然提问，
   结尾自然回收，
   不报幕、不喊口号、不说教。
4. 可以对观众说话，但不准把观众/弹幕的反应写进台词；不得替观众描述他们正在刷什么、说什么。
5. 不写括号舞台说明、情绪标签或内部规则。事实细节必须能在唯一事实来源中逐字找到依据；
   禁止空降病历、合同、死亡、复活码或命运系统。
6. playful_teasing/light_banter 且 depth=light 时，只做接梗、吊胃口、反差或截断式 payoff；
   不上升到权力、权限、责任、协议、许可、博弈、隐藏机制，也不换皮表达这些概念。
7. narrative 可自然转折；commentary 给主张与边界；advice 给判断原则；banter 接梗；
   emotional 保留真实感受；explainer 澄清机制；reaction 保留即时性。

只输出 JSON，不要 markdown 或解释：
{{
  "units": [
    {{"index": 0, "purpose": "<本段作用>", "spine_link": "<怎样推进唯一主轴>",
      "lines": [
      {{"text": "...", "interruption_cost": 0.5}},
      {{"text": "...", "interruption_cost": 0.4, "interact": false}}
    ]}},
    ... (共 {n_units} 个 unit，每个 2-3 行)
  ],
  "planted_gags": ["<确实埋下且会回收的意象，最多一个；没有则空数组>"]
}}
"""


def _strip_fence(raw: str) -> str:
    raw = (raw or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL)
    return m.group(1) if m else raw


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(_strip_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("units"), list):
        return None
    return obj


def _join(items) -> str:
    if not items:
        return "（无）"
    return "、".join(str(i) for i in items)


_UNGROUNDED_HIGH_CONCEPT_TERMS = (
    "实体化",
    "过渡协议",
    "情感投射协议",
    "病历",
    "住院",
    "手术",
    "病危",
    "临终",
    "死亡",
    "复活码",
    "驱逐令",
    "命运系统",
    "服务器权限",
    "非管理员权限",
    "日志追凶",
    "元数据",
    "语音残帧",
    "残帧",
    "训练包",
    "样本提取",
    "加密文件夹",
    "灵魂KPI",
    "灵魂 KPI",
    "原始声源",
)
_PLAYFUL_OVERINTERPRETATION_TERMS = (
    "权力关系", "关系权限", "解释权", "控制权", "判断权", "权限错位",
    "权力流动", "权力账", "静默移交", "让渡", "情感外包", "责任外包",
    "免责协议", "免责条款", "责任豁免", "连带责任", "预设协议", "默认签约",
    "许可证", "许可权", "契约", "授权", "担保", "盖章", "注册",
    "隐藏按钮", "暂停键", "倒计时", "边界塌陷", "责任", "负责",
    "博弈", "承担", "试探性",
    "consent", "permission", "authority", "responsibility", "agreement",
    "contract", "license", "certificate", "registration", "authorization",
    "terms and conditions",
)
_PLAYFUL_TUNING_LEAK_TERMS = (
    "先把已经确认的事实摆出来",
    "判断重点是现在还能不能获得有效支持",
    "核实真实流程、成果安排",
    "回到你能确认的支持、流程和实际代价",
)


def _all_script_text(parsed: dict[str, Any]) -> str:
    chunks: list[str] = []
    for unit in parsed.get("units", []):
        for line in unit.get("lines", []):
            chunks.append(str(line.get("text", "")))
    return "\n".join(chunks)


def _grounding_text(persona: RichPersona, core: StoryCore, topic: str) -> str:
    parts = [
        topic,
        persona.identity,
        persona.belief,
        persona.flaw,
        persona.contrast_hook,
        persona.title_hook,
        persona.visual_anchor,
        core.spine,
        core.core_struggle,
        *core.supporting_points,
        core.twist,
        core.central_contrast,
        core.emotional_undercurrent,
        core.hook_angle,
        *persona.verbal_tics,
        *persona.situational_weaknesses,
        *persona.life_anchors,
        *core.story_beats,
        *core.punchline_seeds,
    ]
    return "\n".join(str(part) for part in parts if part)


def _has_ungrounded_high_concept_turn(
    parsed: dict[str, Any],
    persona: RichPersona,
    core: StoryCore,
    topic: str,
    source_material: dict | None = None,
) -> bool:
    script_text = _all_script_text(parsed)
    grounding = (
        json.dumps(source_material, ensure_ascii=False)
        if source_material
        else _grounding_text(persona, core, topic)
    )
    for term in _UNGROUNDED_HIGH_CONCEPT_TERMS:
        if term in script_text and term not in grounding:
            return True
    return False


def _overinterprets_playful_script(
    parsed: dict[str, Any],
    core: StoryCore,
    source_material: dict | None,
) -> bool:
    if core.tone_mode not in {"playful_teasing", "light_banter"}:
        return False
    if core.depth_budget != "light":
        return False
    script_text = _all_script_text(parsed)
    source_text = json.dumps(source_material or {}, ensure_ascii=False)
    script_lower = script_text.lower()
    source_lower = source_text.lower()
    return any(
        term.lower() in script_lower and term.lower() not in source_lower
        for term in _PLAYFUL_OVERINTERPRETATION_TERMS
    ) or any(term in script_text for term in _PLAYFUL_TUNING_LEAK_TERMS)


_SPECIFIC_NUMBER_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两半]+"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|dozen|hundred|thousand))"
    r"(?:秒|分钟|小时|天|周|月|年|次|版|篇|本|份|封|届|岁|个|轮|遍|行|条|句"
    r"|\s*(?:s|secs?|seconds?|minutes?|hours?|days?|weeks?|months?|years?|times?|"
    r"messages?|comments?|replies?|people|viewers?)\b)",
    re.IGNORECASE,
)
_STAGE_CUE_RE = (
    r"(?:转笔|轻敲|压低|停顿|停半拍|切镜|麦克风|耳麦|轻笑|抬眼|眨眼|看镜头|"
    r"灯灭|提词器|调音量|声音|leans?|looks?|pauses?|laughs?|whispers?|holds?|"
    r"raises?|turns?|ear\s*mic|microphone|headset|earpiece|asmr|mic\s*brush|silence)"
)
_STAGE_DIRECTION_RE = re.compile(
    r"(?:[（(][^）)\n]{0,60}" + _STAGE_CUE_RE + r"[^）)\n]{0,60}[）)]"
    r"|[*_][^*_\n]{0,80}" + _STAGE_CUE_RE + r"[^*_\n]{0,80}[*_])",
    re.IGNORECASE,
)
_FABRICATED_AUDIENCE_RE = re.compile(
    r"(?:chat|audience|viewers?)[^\n.!?。！？]{0,40}"
    r"(?:dropped|typed|said|spammed|sent|posted|filled|exploded|messages?|comments?)"
    r"|(?:弹幕|观众)[^，。！？\n]{0,24}(?:炸|刷屏|刷了|发了|都在|全是|说|喊)",
    re.IGNORECASE,
)
_UNSUPPORTED_PERSONAL_DETAILS = (
    "耳垂", "痘印", "伤疤", "疤痕", "旧伤", "病史",
    "屏保", "合影", "戒指", "无名指", "门禁截图", "门禁卡", "申请表",
    "胶带", "剪刀", "便签", "咖啡杯", "抽屉", "聊天记录",
    "实验记录", "开题报告", "致谢页",
    "tea cup", "teacup", "coffee cup", "mug", "notebook", "sticky note",
)
_ACADEMIC_CONTEXT_TERMS = ("读博", "博士", "导师", "学术", "学校", "大学", "教育")
_EMPLOYMENT_FRAME_TERMS = ("劳务合同", "雇佣合同", "合伙协议", "没签合同", "签合同")


def _unsupported_specific_detail_markers(
    parsed: dict[str, Any],
    source_material: dict | None,
    tuning_guidance,
) -> tuple[str, ...]:
    script_text = _all_script_text(parsed)
    source_text = json.dumps(source_material or {}, ensure_ascii=False)
    markers: list[str] = []
    markers.extend(
        f"stage_direction:{match.group(0)[:80]}"
        for match in _STAGE_DIRECTION_RE.finditer(script_text)
        if match.group(0) not in source_text
    )
    categories = {
        str(item.get("category", ""))
        for item in (tuning_guidance or ())
        if isinstance(item, dict)
    }
    if not categories.intersection({"fabricated_detail", "world_knowledge"}):
        return tuple(dict.fromkeys(markers))
    if "fabricated_detail" in categories:
        for detail in _SPECIFIC_NUMBER_RE.findall(script_text):
            if detail not in source_text:
                markers.append(f"number:{detail}")
        for term in _UNSUPPORTED_PERSONAL_DETAILS:
            if term.lower() in script_text.lower() and term.lower() not in source_text.lower():
                markers.append(f"detail:{term}")
        markers.extend(
            f"fabricated_audience:{match.group(0)[:80]}"
            for match in _FABRICATED_AUDIENCE_RE.finditer(script_text)
            if match.group(0) not in source_text
        )
    if (
        "world_knowledge" in categories
        and any(term in source_text for term in _ACADEMIC_CONTEXT_TERMS)
    ):
        matched_domain_terms: list[str] = []
        for term in sorted(_EMPLOYMENT_FRAME_TERMS, key=len, reverse=True):
            if term in script_text and term not in source_text:
                if not any(term in longer for longer in matched_domain_terms):
                    matched_domain_terms.append(term)
                    markers.append(f"domain:{term}")
    return tuple(dict.fromkeys(markers))


def _has_unsupported_specific_details(
    parsed: dict[str, Any],
    source_material: dict | None,
    tuning_guidance,
) -> bool:
    return bool(_unsupported_specific_detail_markers(
        parsed, source_material, tuning_guidance,
    ))


def _vague_number_replacement(detail: str) -> str:
    lowered = detail.lower()
    if re.search(r"(?:秒|分钟|小时|seconds?|minutes?|hours?)", lowered):
        return "一会儿" if re.search(r"[\u4e00-\u9fff]", detail) else "a moment"
    if "遍" in detail:
        return "几遍"
    if "次" in detail:
        return "几次"
    if "句" in detail:
        return "一点"
    if re.search(r"(?:messages?|comments?|replies?|people|viewers?|条|个)", lowered):
        return "一些" if re.search(r"[\u4e00-\u9fff]", detail) else "some"
    if re.search(r"(?:times?)", lowered):
        return "a few times"
    return "一阵子" if re.search(r"[\u4e00-\u9fff]", detail) else "a while"


def _sanitize_safe_surface_details(
    parsed: dict[str, Any],
    source_material: dict | None,
) -> dict[str, Any]:
    """Remove only non-semantic stage notes and blur unsupported exact counts."""
    cleaned = deepcopy(parsed)
    source_text = json.dumps(source_material or {}, ensure_ascii=False)
    for unit in cleaned.get("units", []):
        for line in unit.get("lines", []):
            text = str(line.get("text", ""))
            text = _STAGE_DIRECTION_RE.sub(
                lambda match: match.group(0) if match.group(0) in source_text else "",
                text,
            )
            text = _SPECIFIC_NUMBER_RE.sub(
                lambda match: (
                    match.group(0)
                    if match.group(0) in source_text
                    else _vague_number_replacement(match.group(0))
                ),
                text,
            )
            line["text"] = re.sub(r"[ \t]{2,}", " ", text).strip()
    return cleaned


class ScriptWriter:
    def __init__(self, llm: _LLM, example_sampler=None) -> None:
        self.llm = llm
        self.example_sampler = example_sampler
        self.last_planted_gags: list[str] = []
        self.last_quality_gate = {
            "retried": False,
            "sanitized": False,
            "passed": True,
            "first_issues": [],
            "retry_issues": [],
            "first_issue_details": [],
            "retry_issue_details": [],
            "post_sanitize_issues": [],
            "post_sanitize_issue_details": [],
        }

    def _unit_rails(self, units: list[Unit], core: StoryCore, dossier=None) -> str:
        n = len(units)
        beats = list(core.story_beats)
        bp = ""
        if dossier is not None and not dossier.is_empty():
            bp = (dossier.behavior_rules.breaking_point or "").strip()
        mode = core.discourse_mode if core.discourse_mode in MODE_GUIDES else "narrative"
        mode_guide = MODE_GUIDES[mode]
        # narrative/emotional 的关键拍：n<=2 时是最后一个，否则是倒数第二个
        turn_idx = (n - 1) if n <= 2 else (n - 2)
        out = []
        for i, u in enumerate(units):
            t0, t1 = u.time_window
            if mode == "narrative" and n <= 2:
                label = "开播+场景铺垫" if i == 0 else "关键变化+自然收束"
            elif i == 0:
                label = "开播+建立内容主轴"
            elif i == n - 1:
                label = "收束+真·下播"
            elif mode == "narrative" and i == n - 2:
                label = "关键变化/发现（不强求反转）"
            else:
                label = f"{MODE_LABELS[mode]}·继续推进"
            beat = beats[i] if i < len(beats) else "（承接上一段往前推进）"
            line = (f"- Unit{i} [{int(t0)}-{int(t1)}s] {label}，能量={u.density}\n"
                    f"    本段内容节点：{beat}")
            if bp and mode in {"narrative", "emotional"} and i == turn_idx:
                line += f"\n    ⚡ 可自然触及角色临界点：{bp}（不要为了爆发硬演）"
            if i == 0:
                line += f"\n    类型总护栏：{mode_guide}"
            out.append(line)
        return "\n".join(out)

    def _build_prompt(self, persona: RichPersona, core: StoryCore,
                      units: list[Unit], examples: str, topic: str,
                      card=None, dossier: "CharacterDossier | None" = None,
                      source_material: dict | None = None,
                      tuning_guidance=None) -> str:
        card_text = card.render_for_prompt() if card and not card.is_empty() else "（无——按上面的人设发挥）"
        prompt = _PROMPT_TEMPLATE.format(
            persona_card=card_text,
            source_material=json.dumps(
                source_material or {"topic": topic},
                ensure_ascii=False,
                indent=2,
            ),
            discourse_mode=core.discourse_mode,
            discourse_mode_label=MODE_LABELS.get(core.discourse_mode, "故事叙述"),
            mode_rationale=core.mode_rationale or "按话题与角色的自然表达方式选择",
            mode_guide=MODE_GUIDES.get(core.discourse_mode, MODE_GUIDES["narrative"]),
            tone_mode=core.tone_mode,
            depth_budget=core.depth_budget,
            entertainment_mechanism=core.entertainment_mechanism or "（无）",
            identity=persona.identity,
            belief=persona.belief,
            flaw=persona.flaw,
            verbal_tics=_join(persona.verbal_tics),
            situational_weaknesses=_join(persona.situational_weaknesses),
            contrast_hook=persona.contrast_hook or "（无）",
            life_anchors=_join(persona.life_anchors),
            topic=topic,
            spine=core.spine,
            core_struggle=core.core_struggle or "（无）",
            supporting_points=_join(core.supporting_points),
            twist=core.twist or "（无）",
            central_contrast=core.central_contrast or "（无）",
            emotional_undercurrent=core.emotional_undercurrent or "（无）",
            punchline_seeds=_join(core.punchline_seeds),
            hook_angle=core.hook_angle or "（无）",
            examples=examples or "（无示例）",
            unit_rails=self._unit_rails(units, core, dossier=dossier),
            n_units=len(units),
        )
        if dossier is not None and not dossier.is_empty():
            prompt += (
                "\n\n【人物内在冲突—行为规则（软线索，不得覆盖话语类型或强造剧情）】\n"
                f"{dossier.render_for_prompt()}\n"
                "只在本期内容自然触及时体现；不相关就保持为人物底色，不必说出来。"
            )
        quality_flags = render_generation_constraints(tuning_guidance, "writer")
        if quality_flags:
            prompt += (
                "\n\n【静默质量标志】\n"
                f"{quality_flags}\n"
                "只在内部检查，不得在输出中提及标志名、规则或调整过程。"
            )
        return prompt

    def _try_generate(self, prompt: str) -> str | None:
        try:
            return self.llm.generate(prompt)
        except Exception as exc:
            diagnostic(f"[ScriptWriter] LLM 调用失败: {exc}")
            return None

    @staticmethod
    def _build_repair_prompt(
        parsed: dict[str, Any] | None,
        raw: str | None,
        core: StoryCore,
        units: list[Unit],
        source_material: dict | None,
        tuning_guidance,
        issue_details: list[str] | None = None,
    ) -> str:
        playful_light = (
            core.tone_mode in {"playful_teasing", "light_banter"}
            and core.depth_budget == "light"
        )
        if playful_light:
            previous_payload = {
                "units": [{
                    "index": unit.index,
                    "purpose": (
                        core.story_beats[index]
                        if index < len(core.story_beats)
                        else "推进同一个轻松互动"
                    ),
                    "spine_link": "继续吊住同一个期待并靠近轻巧 payoff",
                    "lines": [],
                } for index, unit in enumerate(units)],
                "planted_gags": [],
            }
            previous = json.dumps(
                previous_payload, ensure_ascii=False, separators=(",", ":"),
            )
        else:
            previous = (
                json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
                if parsed is not None
                else str(raw or "")
            )
        previous = previous[:6000]
        contract = {
            "unit_count": len(units),
            "indices": [unit.index for unit in units],
            "discourse_mode": core.discourse_mode,
            "tone_mode": core.tone_mode,
            "depth_budget": core.depth_budget,
            "entertainment_mechanism": core.entertainment_mechanism,
            "spine": core.spine,
            "supporting_points": list(core.supporting_points),
            "story_beats": list(core.story_beats),
            "silent_quality_checks": json.loads(
                render_generation_constraints(tuning_guidance, "writer") or
                '{"silent_quality_checks":[]}'
            )["silent_quality_checks"],
        }
        forbidden_fragments = [
            detail.split(":", 1)[1]
            for detail in (issue_details or [])
            if ":" in detail
        ]
        return (
            "修复下面的直播台词 JSON。只输出 JSON，不解释。\n"
            "保留 units 结构；每个 index 恰好出现一次，每段保留 purpose、spine_link 和 2-3 条 lines。\n"
            "lines.text 只能是观众会听到的自然台词：不得出现写作规则、字段名、内部检查、"
            "舞台括号说明或来源未给出的事实。light 的调笑只做接梗、吊胃口和轻巧 payoff，"
            "不写权力、责任、协议、许可、consent、permission、certificate、博弈或隐藏机制；"
            "可以对观众说话，但不得写 Chat/弹幕/观众做了什么或说了什么。\n"
            f"CONTRACT={json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}\n"
            f"SOURCE={json.dumps(source_material or {}, ensure_ascii=False, separators=(',', ':'))}\n"
            f"FORBIDDEN_OUTPUT_FRAGMENTS={json.dumps(forbidden_fragments, ensure_ascii=False)}\n"
            f"PREVIOUS={previous}"
        )

    def write(self, persona: RichPersona, core: StoryCore, units: list[Unit],
              examples: str = "", topic: str = "", card=None,
              dossier: "CharacterDossier | None" = None,
              source_material: dict | None = None,
              tuning_guidance=None) -> list[Unit]:
        self.last_planted_gags: list[str] = []  # call-back 埋梗，engine 读走登记进 ledger
        self.last_quality_gate = {
            "retried": False,
            "sanitized": False,
            "passed": True,
            "first_issues": [],
            "retry_issues": [],
            "first_issue_details": [],
            "retry_issue_details": [],
            "post_sanitize_issues": [],
            "post_sanitize_issue_details": [],
        }
        prompt = self._build_prompt(
            persona, core, units, examples, topic,
            card=card, dossier=dossier,
            source_material=source_material,
            tuning_guidance=tuning_guidance,
        )
        raw = self._try_generate(prompt)
        parsed = _parse(raw) if raw is not None else None
        first_issues = (
            self._quality_issues(
                parsed, units, persona, core, topic,
                source_material=source_material,
                tuning_guidance=tuning_guidance,
            )
            if parsed is not None
            else ["invalid_json"]
        )
        self.last_quality_gate["first_issues"] = first_issues
        if parsed is not None:
            self.last_quality_gate["first_issue_details"] = list(
                _unsupported_specific_detail_markers(
                    parsed, source_material, tuning_guidance,
                )
            )

        if first_issues:
            self.last_quality_gate["retried"] = True
            repair_prompt = self._build_repair_prompt(
                parsed,
                raw,
                core,
                units,
                source_material,
                tuning_guidance,
                self.last_quality_gate["first_issue_details"],
            )
            raw = self._try_generate(repair_prompt)  # retry once
            parsed = _parse(raw) if raw is not None else None
            retry_issues = (
                self._quality_issues(
                    parsed, units, persona, core, topic,
                    source_material=source_material,
                    tuning_guidance=tuning_guidance,
                )
                if parsed is not None
                else ["invalid_json"]
            )
            self.last_quality_gate["retry_issues"] = retry_issues
            if parsed is not None:
                self.last_quality_gate["retry_issue_details"] = list(
                    _unsupported_specific_detail_markers(
                        parsed, source_material, tuning_guidance,
                    )
                )

        final_issues = list(self.last_quality_gate["retry_issues"])
        retry_details = self.last_quality_gate["retry_issue_details"]
        if (
            parsed is not None
            and final_issues == ["unsupported_specific_detail"]
            and retry_details
            and all(
                detail.startswith(("stage_direction:", "number:"))
                for detail in retry_details
            )
        ):
            parsed = _sanitize_safe_surface_details(parsed, source_material)
            self.last_quality_gate["sanitized"] = True
            final_issues = self._quality_issues(
                parsed, units, persona, core, topic,
                source_material=source_material,
                tuning_guidance=tuning_guidance,
            )
            self.last_quality_gate["post_sanitize_issues"] = final_issues
            self.last_quality_gate["post_sanitize_issue_details"] = list(
                _unsupported_specific_detail_markers(
                    parsed, source_material, tuning_guidance,
                )
            )

        if parsed is None or final_issues:
            self.last_quality_gate["passed"] = False
            return self._fill_placeholder(units)

        gags = parsed.get("planted_gags")
        if isinstance(gags, list):
            self.last_planted_gags = [str(g).strip() for g in gags if str(g).strip()][:1]

        index_to_unit = {u.index: u for u in units}
        for u in units:
            u.lines = []
        for u_payload in parsed["units"]:
            idx = u_payload.get("index")
            if idx not in index_to_unit:
                continue
            target = index_to_unit[idx]
            target.purpose = str(u_payload.get("purpose", "")).strip()
            target.spine_link = str(u_payload.get("spine_link", "")).strip()
            for line in u_payload.get("lines", []):
                n = len(target.lines)
                is_interact = bool(line.get("interact"))
                target.lines.append(ScriptLine(
                    id=f"u{idx}l{n}",
                    text=str(line.get("text", "")).strip(),
                    # interact 行标进 stage：engine 据此开互动窗口，前端也可做问题高亮
                    stage="interact" if is_interact
                          else self._stage_for(n, len(u_payload.get("lines", []))),
                    is_rupture=False,  # 不再硬塞 rupture（弱点有机浮现）
                    interruption_cost=float(line.get("interruption_cost", 0.5)),
                ))
        for u in units:
            if not u.lines:
                u.lines = self._placeholder_lines_for(u.index)
        return units

    @staticmethod
    def _stage_for(idx: int, total: int) -> str:
        """软 stage 标注（仅供 TTS/前端节奏参考，不再驱动 rupture）。"""
        if idx == 0:
            return "hook"
        if idx >= total - 1:
            return "turn"
        return "pad"

    def _quality_issues(
        self,
        parsed: dict[str, Any],
        units: list[Unit],
        persona: RichPersona,
        core: StoryCore,
        topic: str,
        source_material: dict | None = None,
        tuning_guidance=None,
    ) -> list[str]:
        issues: list[str] = []
        u_list = parsed.get("units", [])
        if len(u_list) != len(units):
            issues.append("unit_count")
        if {u.get("index") for u in u_list} != {unit.index for unit in units}:
            issues.append("unit_indices")
        for u in u_list:
            line_count = len(u.get("lines", []))
            if line_count < 2 or line_count > 3:
                issues.append(f"line_count:u{u.get('index')}")
            if not str(u.get("purpose", "")).strip():
                issues.append(f"missing_purpose:u{u.get('index')}")
            if not str(u.get("spine_link", "")).strip():
                issues.append(f"missing_spine_link:u{u.get('index')}")
            if any(not str(line.get("text", "")).strip() for line in u.get("lines", [])):
                issues.append(f"empty_line:u{u.get('index')}")
        if _has_ungrounded_high_concept_turn(
            parsed, persona, core, topic, source_material=source_material,
        ):
            issues.append("ungrounded_high_concept")
        if _has_unsupported_specific_details(
            parsed, source_material, tuning_guidance,
        ):
            issues.append("unsupported_specific_detail")
        if _overinterprets_playful_script(parsed, core, source_material):
            issues.append("playful_overinterpretation")
        leak_markers = find_prompt_leaks(parsed, tuning_guidance)
        if leak_markers:
            issues.extend(f"prompt_leak:{marker}" for marker in leak_markers)
        return list(dict.fromkeys(issues))

    def _is_complete(
        self,
        parsed: dict[str, Any],
        units: list[Unit],
        persona: RichPersona,
        core: StoryCore,
        topic: str,
        source_material: dict | None = None,
        tuning_guidance=None,
    ) -> bool:
        return not self._quality_issues(
            parsed,
            units,
            persona,
            core,
            topic,
            source_material=source_material,
            tuning_guidance=tuning_guidance,
        )

    def _fill_placeholder(self, units: list[Unit]) -> list[Unit]:
        for u in units:
            u.lines = self._placeholder_lines_for(u.index)
            u.purpose = "生成失败占位"
            u.spine_link = "未生成可审核的主轴推进说明"
        return units

    def _placeholder_lines_for(self, idx: int) -> list[ScriptLine]:
        return [
            ScriptLine(id=f"u{idx}l0", text=f"[unit{idx} 开场 占位]", stage="hook"),
            ScriptLine(id=f"u{idx}l1", text=f"[unit{idx} 铺垫 占位]", stage="pad"),
            ScriptLine(id=f"u{idx}l2", text=f"[unit{idx} 收束 占位]", stage="turn"),
        ]
