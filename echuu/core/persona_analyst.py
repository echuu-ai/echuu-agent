"""PersonaAnalyst (agent①) — 从用户设定推导 RichPersona + StoryCore。

单次 LLM 调用，输出严格 JSON 两块：
  - persona: 语癖 / 情境弱点 / 反差悬疑 / 生活化锚点 + 身份/信念/弱点
  - story:   主线 / 核心反差 / 情绪暗线 / 金句种子 / 钩子角度

绝不抛错崩 setup()——LLM 失败时退化到占位（flaw 永不为空）。
See docs/superpowers/specs/2026-05-31-rupture-engine-content-quality-design.md §3.
"""
from __future__ import annotations

import json
import re
from typing import Any, Protocol

from echuu.core.discourse_mode import (
    DISCOURSE_MODES,
    MODE_GUIDES,
    normalize_discourse_mode,
)
from echuu.core.persona_card import PersonaCard
from echuu.core.persona_dossier import CharacterDossier
from echuu.core.persona_model import RichPersona
from echuu.core.prompt_leakage import find_prompt_leaks
from echuu.core.story_core import StoryCore
from echuu.core.tuning_guidance import (
    normalize_tuning_guidance,
    render_generation_constraints,
)
from echuu.diagnostics import diagnostic


class _LLM(Protocol):
    def generate(self, prompt: str) -> str: ...


PROMPT_VERSION = "persona-analyst-v5-tone-depth"


TONE_MODES = {
    "conversational",
    "playful_teasing",
    "light_banter",
    "warm",
    "serious",
    "reflective",
    "informative",
    "energetic",
}
DEPTH_BUDGETS = {"light", "medium", "deep"}
PLAYFUL_TONES = {"playful_teasing", "light_banter"}
_OVERINTERPRETATION_TERMS = (
    "权力关系", "关系权限", "解释权", "控制权", "判断权", "权限错位",
    "权力流动", "权力账", "静默移交", "让渡", "情感外包", "责任外包",
    "免责协议", "免责条款", "责任豁免", "连带责任", "预设协议", "签约",
    "许可证", "许可权", "契约", "授权", "担保", "盖章", "注册",
    "隐藏按钮", "暂停键", "倒计时", "边界塌陷", "责任", "负责",
    "博弈", "承担", "试探性",
    "consent", "permission", "authority", "responsibility", "agreement",
    "contract", "license", "certificate", "registration", "authorization",
    "terms and conditions",
)
_FABRICATED_STAGE_RE = re.compile(
    r"(?:[（(][^）)\n]{0,60}(?:转笔|轻敲|压低|停顿|停半拍|切镜|麦克风|耳麦|"
    r"轻笑|抬眼|眨眼|看镜头|灯灭|提词器|调音量|声音|leans?|looks?|pauses?|"
    r"laughs?|whispers?|holds?|raises?|turns?|ear\s*mic|microphone|headset|earpiece|"
    r"asmr|mic\s*brush|silence)[^）)\n]{0,60}[）)]"
    r"|[*_][^*_\n]{0,80}(?:轻敲|停顿|耳麦|麦克风|眨眼|调音量|leans?|pauses?|"
    r"laughs?|whispers?|ear\s*mic|microphone|headset|earpiece|asmr|mic\s*brush|silence)"
    r"[^*_\n]{0,80}[*_])",
    re.IGNORECASE,
)


def normalize_tone_mode(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in TONE_MODES else "conversational"


def normalize_depth_budget(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in DEPTH_BUDGETS else "medium"


_PROMPT = """\
你负责直播内容的人物策划。读完下面这位主播的设定，做三件事：
(1) 把他/她抽象成一个有自己看法的活人——不是复述设定，而是挖出能让人忍不住听下去的活人细节；
(2) 判断这期内容最自然的话语类型，不要把所有内容都硬写成故事；
(3) 提炼这个人在这个话题上凭什么值得听，写成内容内核。
最重要的原则：**有意思不来自事情本身离奇——事要普通、接地气，普通到人人都经历过才可信；
有意思来自这个人对这件普通小事的【独特看法和洞察】**。好主播的做法是：拿一件人人都干过的
小事当由头，把它翻成一个观众没想过、但一听就认的歪理——事只是入口，看法才是节目。
所以别为了精彩去编离奇情节：生活化反差、小尴尬、小账（钱/时间/面子）足够了。
禁止靠无铺垫的大设定硬炸：实体化协议、住院病历号、死亡/临终、复活码、命运系统、
服务器权限、日志追凶、语音残帧这类技术悬疑/科幻/创伤设定，除非用户话题本身明确要求。

主播：{name}
人设：{persona}
背景：{background}
今天的话题：{topic}
用户指定语气：{tone_intent}
深度预算：{depth_budget}
娱乐/喜剧机制：{entertainment_mechanism}

严格输出 JSON（不要任何额外文字、不要 markdown 围栏）：
{{
  "persona": {{
    "identity": "<观众秒认的身份，一句话>",
    "belief": "<他挂在嘴边、会被现实打脸的信念，一句话>",
    "flaw": "<具体到一个场景的弱点/反常点，越具体越好；写成「一遇到某情境就做出某反常动作」>",
    "verbal_tics": ["<语癖/口头禅，2-4个，必须贴合这个人的年龄、职业和说话方式现编，不要用通用口头禅>"],
    "situational_weaknesses": ["<情境弱点，1-3条，格式：一遇到X就Y>"],
    "contrast_hook": "<反差/悬疑：让观众对这个人产生「等下，这人怎么回事」那种张力，一句话>",
    "life_anchors": ["<最多1-2个、且必须能从用户原始人设/背景/话题/人设卡找到依据的具体物品、场景或动作；没有依据就留空>"],
    "title_hook": "<像标题，让人划到不想走，<=30字>",
    "visual_anchor": "<贯穿性视觉锚，如配饰/小动作/背景物>"
  }},
  "story": {{
    "discourse_mode": "<narrative|commentary|advice|banter|emotional|explainer|reaction 之一>",
    "mode_rationale": "<为什么这种话语类型最符合话题和主播，一句话>",
    "tone_mode": "<conversational|playful_teasing|light_banter|warm|serious|reflective|informative|energetic 之一>",
    "depth_budget": "<light|medium|deep 之一>",
    "entertainment_mechanism": "<这轮靠什么好看，例如故作认真、吊胃口、误会、callback；没有就留空>",
    "spine": "<本期唯一中心结论/回答，一句话说清观众听完应记住什么；不要只是宽泛题目>",
    "core_struggle": "<真正驱动内容的问题/矛盾/判断点；不要求每种类型都有戏剧冲突>",
    "supporting_points": [
      "<直接支持 spine 的第一条依据/推进>",
      "<可选的第二条依据/推进；没有必要就不要凑>"
    ],
    "twist": "<关键推进或 payoff；故事可用转折，观点可用反例，建议可用决策原则，解释可用澄清；不需要时可为空>",
    "central_contrast": "<最值得展开的差异/边界/反差；不适用时可为空>",
    "emotional_undercurrent": "<情绪暗线：从什么情绪走到什么情绪>",
    "story_beats": [
      "<建立唯一主轴，不引入第二个问题>",
      "<用 supporting_points 推进，不能另起话题或另换框架>",
      "<给出该类型的关键判断/payoff/边界并自然收束>"
    ],
    "punchline_seeds": ["<最多1-2个洞察种子，必须服务 spine；不要同时提供多套比喻或概念框架>"],
    "hook_angle": "<开场前30-60秒怎么抓人，一句话>"
  }}
}}

可选话语类型与推荐结构：
{mode_guides}

要点：
- 用户指定语气、深度预算、娱乐/喜剧机制不是参考意见，而是本轮创作合同。语义内容和表达姿态
  是两条轴：即使提到词义或边界，playful_teasing 也应靠故作认真、暧昧吊胃口或轻巧反转推进，
  不能自动升级成严肃评论。
- depth_budget=light 时，只允许一个贴近表面的观察或笑点，不做关系、权力、责任、协议、
  解释权等社会理论化延伸；中心主轴应描述这轮互动的期待、误会或 payoff，而不是宏大结论。
- playful_teasing/light_banter 的高光优先是抖包袱、callback、反差或一句俏皮回答，
  不是去揭示什么隐藏结构。除非原始输入明确要求，不得写权限移交、情感外包、免责协议、
  连带责任、解释权让渡之类概念，也不要换成许可证、契约、授权、盖章、隐藏按钮或倒计时
  继续包装同一套抽象解释。指定了喜剧机制时直接用它，不另造第二套中心隐喻。
- 先做删减检查：删掉某个 supporting_point 或 beat，如果不影响 spine 的成立，就删掉它。
- supporting_points 最多 2 条，是证据、条件或推进，不是新的故事线。
- story_beats 通常只用 2-3 个；只有 narrative 且确有必要的完整时间线才可用 4 个。
  它们必须服务同一 spine，不得每段换一个隐喻/框架，也不得把同一点换句话重复。
- 只有 narrative 需要优先考虑场景发展和转折；其他类型按各自结构推进，不得硬塞戏剧冲突、
  虚构经历、认知翻转或治愈式结尾。
- life_anchors、数字、身体特征和经历只有输入中有依据才能给；具体不等于编得精细。
- Dossier 是机器提出的人物假设，不是事实来源；不得从中提取日期、数量、身体特征、私人物件或往事。
- supporting_points 中的事实也必须来自原始输入。输入没有足够事实时，写成判断条件或一般原则，
  不得为了让骨架显得具体而补造论文数、基金申请、聊天记录或亲历案例。
- **按这件事本身撑得起多少来定阶段数**。宁可短而精，不要长而散。
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
    if not isinstance(obj, dict):
        return None
    if not isinstance(obj.get("persona"), dict) or not isinstance(obj.get("story"), dict):
        return None
    return obj


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


_UNSUPPORTED_FACT_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万两半]+)"
    r"(?:秒|分钟|小时|天|周|月|年|次|版|篇|本|份|封|届|岁|个|轮|遍|行|条|句)"
)
_UNSUPPORTED_FACT_TERMS = (
    "耳垂", "痘印", "伤疤", "疤痕", "旧伤", "病史",
    "屏保", "合影", "戒指", "无名指", "门禁截图", "门禁卡",
    "申请表", "胶带", "剪刀", "便签", "咖啡杯", "抽屉", "聊天记录",
    "实验记录", "开题报告", "致谢页",
    "劳务合同", "雇佣合同", "合伙协议", "没签合同",
)
_SAFE_BEATS = {
    "narrative": ("建立输入已有的场景与问题", "推进同一事件中的变化", "落到主轴对应的结果"),
    "commentary": ("明确唯一主张", "给出可确认的依据与边界", "回到主张并回应适用条件"),
    "advice": ("复述需要判断的问题", "核对可确认的条件与代价", "给出判断原则与适用边界"),
    "banter": ("接住当前互动", "围绕同一话题推进回应", "自然回收前面的互动"),
    "emotional": ("说清当下感受", "说明感受来自什么已知处境", "允许情绪停在真实位置"),
    "explainer": ("界定要解释的问题", "说明关键机制与条件", "澄清边界和常见误解"),
    "reaction": ("给出即时反应", "说明反应依据", "保留开放判断或下一步"),
}
_SAFE_SUPPORTING_POINTS = {
    "advice": (
        "确认当前是否仍能获得实现目标所需的有效支持",
        "比较继续与改变各自可确认的代价、流程和边界",
    ),
    "commentary": ("先区分事实判断与价值判断", "说明主张成立与不成立的边界"),
    "explainer": ("先确认概念与实际机制", "再说明适用条件与例外"),
}


def _uses_factuality_gate(tuning_guidance) -> bool:
    categories = {
        item["category"] for item in normalize_tuning_guidance(tuning_guidance)
    }
    return bool(categories.intersection({"fabricated_detail", "world_knowledge"}))


def _contains_unsupported_fact(text: str, source_text: str) -> bool:
    for detail in _UNSUPPORTED_FACT_RE.findall(text or ""):
        if detail not in source_text:
            return True
    for term in _UNSUPPORTED_FACT_TERMS:
        if term in (text or "") and term not in source_text:
            return True
    return False


def _overinterprets_playful_content(
    story: dict[str, Any],
    tone_mode: str,
    depth_budget: str,
    source_text: str,
) -> bool:
    if tone_mode not in PLAYFUL_TONES or depth_budget != "light":
        return False
    story_text = json.dumps(story, ensure_ascii=False)
    story_lower = story_text.lower()
    source_lower = source_text.lower()
    has_abstract_reframe = any(
        term.lower() in story_lower and term.lower() not in source_lower
        for term in _OVERINTERPRETATION_TERMS
    )
    has_fabricated_stage = any(
        match.group(0) not in source_text
        for match in _FABRICATED_STAGE_RE.finditer(story_text)
    )
    return has_abstract_reframe or has_fabricated_stage


def _playful_story_placeholder(
    topic: str,
    tone_mode: str,
    entertainment_mechanism: str,
) -> StoryCore:
    mechanism = entertainment_mechanism or "轻松接梗、吊住期待、用反差收尾"
    return StoryCore(
        spine=f"围绕「{topic}」接住玩笑、吊住期待，再用一个轻巧回应收尾",
        discourse_mode="banter",
        mode_rationale="用户指定轻松调笑，内容重点是互动与 payoff，不做理论化解读",
        tone_mode=tone_mode,
        depth_budget="light",
        entertainment_mechanism=mechanism,
        core_struggle="怎样在不把玩笑说重的前提下，让观众继续期待下一句",
        supporting_points=("先接住表面互动", "让反差或回应承担高光"),
        twist=mechanism,
        central_contrast="表面故作认真，实际在吊胃口或玩梗",
        emotional_undercurrent="好奇 → 被吊住 → 会心一笑",
        story_beats=("接住当前玩笑", "围绕同一期待轻轻推进", "用反差或回应自然收束"),
        punchline_seeds=(),
        hook_angle="从观众刚抛来的话头直接接梗，不先发表严肃结论",
    )


# 退化占位符会被原样注入 ScriptWriter 的 prompt，所以不能再用 [待补充X] 这类标记
# 文本——它一旦被模型当成台词抄出去就是穿帮。改成说出来也不违和的中性描述，
# 「本轮是否退化」改用 is_placeholder_persona() 判断，不再依赖字面标记。
_PH_BELIEF = "先把话说满，回头再找补"
_PH_FLAW = "面对这个话题时容易把判断说得过满"
_PH_CONTRAST = "嘴上笃定，具体细节反而说不清"


def is_placeholder_persona(rich_persona: RichPersona) -> bool:
    """判断 RichPersona 是否来自退化兜底（供 engine / debug_trace 标记状态用）。"""
    return rich_persona.belief == _PH_BELIEF and rich_persona.flaw == _PH_FLAW


def _placeholder(name: str, persona: str, topic: str) -> tuple[RichPersona, StoryCore]:
    _head_src = (persona or name or "未提供").strip()
    head = (_head_src.splitlines()[0][:40] if _head_src else "") or "这位主播"
    rp = RichPersona(
        identity=head,
        belief=_PH_BELIEF,
        flaw=_PH_FLAW,                            # I4: 非空
        verbal_tics=("那个", "就是说"),
        situational_weaknesses=("一紧张就开始绕圈子",),
        contrast_hook=_PH_CONTRAST,
        life_anchors=(),
        title_hook="",
        visual_anchor="",
    )
    sc = StoryCore(
        spine=(f"关于「{topic}」的一段亲身经历" if topic
               else "围绕当前话题给出一个清晰判断"),
        discourse_mode=normalize_discourse_mode(None, topic, persona),
        mode_rationale="LLM 不可用，按话题关键词选择兜底结构",
        core_struggle="在这件事上到底该怎么判断",
        twist="",
        central_contrast=_PH_CONTRAST,
        emotional_undercurrent="从犹豫走到能说出一个判断",
        supporting_points=(),
        story_beats=("建立主轴", "给出依据", "落到判断或边界"),
        punchline_seeds=(),
        hook_angle="从这件事最普通的那一面切进去",
    )
    return rp, sc


class PersonaAnalyst:
    """从用户设定推导 RichPersona + StoryCore（单次 LLM 调用 + 退化兜底）。"""

    def analyze(
        self,
        name: str,
        persona: str,
        topic: str,
        background: str,
        llm: _LLM,
        card: "PersonaCard | None" = None,
        dossier: "CharacterDossier | None" = None,
        tuning_guidance=None,
        tone_intent: str = "auto",
        depth_budget: str = "auto",
        entertainment_mechanism: str = "",
    ) -> tuple[RichPersona, StoryCore]:
        requested_tone = (
            normalize_tone_mode(tone_intent)
            if str(tone_intent or "").strip().lower() != "auto"
            else ""
        )
        requested_depth = (
            normalize_depth_budget(depth_budget)
            if str(depth_budget or "").strip().lower() != "auto"
            else ""
        )
        prompt = _PROMPT.format(
            name=name or "", persona=persona or "", background=background or "", topic=topic or "",
            tone_intent=requested_tone or "auto（由话题自然判断）",
            depth_budget=requested_depth or "auto（按内容承载力判断）",
            entertainment_mechanism=entertainment_mechanism or "（未指定）",
            mode_guides="\n".join(
                f"- {mode}: {MODE_GUIDES[mode]}" for mode in DISCOURSE_MODES
            ),
        )
        if dossier is not None and not dossier.is_empty():
            # 人物小传是跨场背景，不强迫每种话语类型都围绕内在冲突写剧情。
            prompt += (
                "\n\n【人物小传与内在冲突（保持人物一致，但不要覆盖本期自然的话语类型）】\n"
                f"{dossier.render_for_prompt()}\n"
                "narrative/emotional 可把它作为潜在张力；commentary/advice/banter/explainer/reaction "
                "只在自然相关时采用。它不是事实来源，不得把假设升级成身体特征、日期、数量、"
                "物件或人物履历；不得为了用上冲突而虚构经历或强造 twist。"
            )
        if card and not card.is_empty():
            # 用户人设卡是硬约束：口癖/称呼/雷点等直接采用，不许 analyst 自己发明
            prompt += (
                "\n\n【用户人设卡（硬约束，分析结果必须与之一致）】\n"
                f"{card.render_for_prompt()}\n"
                "verbal_tics 直接输出卡中口癖；雷点/骄傲点可作为 situational_weaknesses 与 flaw 的素材。"
            )
        quality_flags = render_generation_constraints(tuning_guidance, "analyst")
        if quality_flags:
            prompt += (
                "\n\n【静默质量标志】\n"
                f"{quality_flags}\n"
                "只在内部检查，不得在输出中提及标志名、规则或调整过程。"
            )
        last_rich_persona: RichPersona | None = None
        rejected_playful_tone = ""
        for _ in range(2):  # one retry
            try:
                raw = llm.generate(prompt)
            except Exception as exc:
                diagnostic(f"[PersonaAnalyst] LLM 调用失败: {exc}")
                continue
            obj = _parse(raw)
            if not obj:
                continue
            if find_prompt_leaks(obj, tuning_guidance):
                continue
            p, s = obj["persona"], obj["story"]
            identity = str(p.get("identity", "")).strip()
            belief = str(p.get("belief", "")).strip()
            flaw = str(p.get("flaw", "")).strip()
            if not (identity and belief and flaw):
                continue  # 三轴缺失 → 重试/退化
            source_text = "\n".join(filter(None, (
                name, persona, background, topic,
                card.render_for_prompt() if card and not card.is_empty() else "",
            )))
            use_gate = _uses_factuality_gate(tuning_guidance)
            life_anchors = _as_tuple(p.get("life_anchors"))[:2]
            visual_anchor = str(p.get("visual_anchor", "")).strip()
            situational_weaknesses = _as_tuple(p.get("situational_weaknesses"))
            contrast_hook = str(p.get("contrast_hook", "")).strip()
            title_hook = str(p.get("title_hook", "")).strip()
            if use_gate:
                life_anchors = tuple(
                    anchor for anchor in life_anchors if anchor in source_text
                )
                if visual_anchor not in source_text:
                    visual_anchor = ""
                if _contains_unsupported_fact(flaw, source_text):
                    flaw = "面对这个话题时容易把判断说得过满"
                situational_weaknesses = tuple(
                    item for item in situational_weaknesses
                    if not _contains_unsupported_fact(item, source_text)
                )
                if _contains_unsupported_fact(contrast_hook, source_text):
                    contrast_hook = ""
                if _contains_unsupported_fact(title_hook, source_text):
                    title_hook = ""
            rp = RichPersona(
                identity=identity,
                belief=belief,
                flaw=flaw,
                # 卡片口癖钉死（跨场稳定是"角色认得出来"的根基）
                verbal_tics=(card.speech_tics if card and card.speech_tics
                             else _as_tuple(p.get("verbal_tics"))),
                situational_weaknesses=situational_weaknesses,
                contrast_hook=contrast_hook,
                life_anchors=life_anchors,
                title_hook=title_hook,
                visual_anchor=visual_anchor,
            )
            last_rich_persona = rp
            mode = normalize_discourse_mode(
                str(s.get("discourse_mode", "")), topic, persona, background,
            )
            tone_mode = requested_tone or normalize_tone_mode(s.get("tone_mode"))
            resolved_depth = requested_depth or normalize_depth_budget(s.get("depth_budget"))
            resolved_mechanism = (
                str(entertainment_mechanism).strip()
                or str(s.get("entertainment_mechanism", "")).strip()
            )
            if _overinterprets_playful_content(
                s, tone_mode, resolved_depth, source_text,
            ):
                # 记下模型自己选的语气：退化时据此抢救 persona（用户可能没指定语气）
                rejected_playful_tone = tone_mode
                prompt += (
                    "\n\n【上一稿语气守门失败，请完整重写】\n"
                    "这是 light 的轻松调笑，不是社会议题评论。删除权力、权限、协议、免责、"
                    "责任转移、解释权等抽象理论；主轴改成互动期待、故作认真、吊胃口、"
                    "接梗或轻巧反转。仍只输出规定 JSON。"
                )
                continue
            max_beats = 4 if mode == "narrative" else 3
            spine = str(s.get("spine", "")).strip() or f"围绕「{topic}」给出清晰判断"
            supporting_points = _as_tuple(s.get("supporting_points"))[:2]
            story_beats = _as_tuple(s.get("story_beats"))[:max_beats]
            punchline_seeds = _as_tuple(s.get("punchline_seeds"))[:2]
            twist = str(s.get("twist", "")).strip()
            central_contrast = str(s.get("central_contrast", "")).strip()
            hook_angle = str(s.get("hook_angle", "")).strip()
            if use_gate:
                if _contains_unsupported_fact(spine, source_text):
                    if tone_mode in PLAYFUL_TONES and resolved_depth == "light":
                        spine = (
                            f"围绕「{topic}」接住玩笑、吊住期待，"
                            "再用一个轻巧回应收尾"
                        )
                    else:
                        spine = f"围绕「{topic}」给出基于可确认事实的判断"
                supporting_points = tuple(
                    point for point in supporting_points
                    if not _contains_unsupported_fact(point, source_text)
                )
                if mode in _SAFE_SUPPORTING_POINTS:
                    supporting_points = _SAFE_SUPPORTING_POINTS[mode]
                story_beats = tuple(
                    beat for beat in story_beats
                    if not _contains_unsupported_fact(beat, source_text)
                )
                if len(story_beats) < 2:
                    story_beats = _SAFE_BEATS[mode]
                punchline_seeds = tuple(
                    seed for seed in punchline_seeds
                    if not _contains_unsupported_fact(seed, source_text)
                )
                if _contains_unsupported_fact(twist, source_text):
                    twist = ""
                if _contains_unsupported_fact(central_contrast, source_text):
                    central_contrast = ""
                if _contains_unsupported_fact(hook_angle, source_text):
                    hook_angle = ""
            sc = StoryCore(
                spine=spine,
                discourse_mode=mode,
                mode_rationale=str(s.get("mode_rationale", "")).strip(),
                tone_mode=tone_mode,
                depth_budget=resolved_depth,
                entertainment_mechanism=resolved_mechanism,
                core_struggle=str(s.get("core_struggle", "")).strip(),
                supporting_points=supporting_points,
                twist=twist,
                central_contrast=central_contrast,
                emotional_undercurrent=str(s.get("emotional_undercurrent", "")).strip(),
                story_beats=story_beats,
                punchline_seeds=punchline_seeds,
                hook_angle=hook_angle,
            )
            return rp, sc
        # 退化路径。语气守门拒的是 story 的抽象化，persona 本身仍然可用——
        # 抢救语气优先取用户指定值，用户没指定（tone_intent=auto，默认值）时
        # 取模型自己选中、随后被拒的那个语气；否则整场人设会退化成硬编码占位符。
        salvage_tone = (
            requested_tone
            if (requested_tone in PLAYFUL_TONES and requested_depth == "light")
            else rejected_playful_tone
        )
        if last_rich_persona is not None and salvage_tone in PLAYFUL_TONES:
            return last_rich_persona, _playful_story_placeholder(
                topic, salvage_tone, entertainment_mechanism,
            )
        return _placeholder(name, persona, topic)
