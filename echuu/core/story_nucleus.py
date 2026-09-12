"""
故事内核生成器 - 分享欲 + 反常 + 内心戏
"""

import random


class StoryNucleus:
    """
    故事内核生成器

    核心：精彩 = 分享欲 + 反常 + 内心戏
    """

    SHARING_URGES = {
        "sensory_trigger": {
            "description": "感官触发回忆",
            "pattern": "刚刚感受到什么 → 回忆涌上来 → 必须说",
            "opening_templates": [
                "我刚才{action}，突然想起一个事...",
                "{sensory}这个味道/画面，让我想起...",
                "诶看到这个我突然想起...",
            ],
            "compatible_nucleus": ["slippery_slope", "kindness_trap"],
        },
        "pain_point_hit": {
            "description": "被戳到痛点",
            "pattern": "被触发 → 愤怒/委屈 → 必须反驳",
            "opening_templates": [
                "每次有人跟我说{trigger}，我就...",
                "我最烦的就是{trigger}...",
                "刚刚弹幕说{trigger}，我必须说一下...",
            ],
            "compatible_nucleus": ["anger_armor", "choice_cost"],
        },
        "absurdity_overflow": {
            "description": "太离谱必须吐槽",
            "pattern": "遇到离谱的事 → 装不下了 → 必须说",
            "opening_templates": [
                "你们猜怎么着...",
                "我跟你们说个离谱的事...",
                "笑死我了，{person}跟我说...",
            ],
            "compatible_nucleus": ["tiny_shame", "contradiction_reveal"],
        },
        "secret_confession": {
            "description": "想坦白",
            "pattern": "一直没说 → 今天想说 → 说出来会轻松",
            "opening_templates": [
                "其实我一直没跟人说过...",
                "我今天想坦白一个事...",
                "这个事我从来没说过，但是...",
            ],
            "compatible_nucleus": ["choice_cost", "slippery_slope"],
        },
        "unfinished_processing": {
            "description": "想不明白的事",
            "pattern": "一直在想 → 没想明白 → 说出来帮我理解",
            "opening_templates": [
                "我一直在想一个事...",
                "这件事我到现在都不知道为什么...",
                "有个事我一直想不明白...",
            ],
            "compatible_nucleus": ["kindness_trap", "contradiction_reveal"],
        },
    }

    ABNORMALITIES = {
        "behavior": "做了不该做的事（偷吃、说谎、逃避）",
        "reaction": "对正常事有奇怪反应（善意→想逃）",
        "logic": "自欺欺人的推理（用一个自认为合理的小动作掩盖→以为没人发现）",
        "identity": "和人设/期待不符（想当老师→不喜欢小孩）",
        "outcome": "结局出乎意料（被发现→对方早就知道）",
        "perception": "别人觉得是A，对你是B（夸奖→侮辱）",
    }

    NUCLEUS_PATTERNS = {
        "slippery_slope": {
            "name": "心理滑坡",
            "sharing_urge": ["sensory_trigger", "secret_confession"],
            "abnormality": ["behavior", "logic"],
            "structure": [
                ("trigger", "分享欲触发：{urge_template}"),
                ("temptation", "小诱惑出现"),
                ("first_slip", "第一次妥协 + 自我说服"),
                ("repetition", "重复（每次都是'最后一次'）"),
                ("discovery", "发现失控"),
                ("absurd_cover", "笨拙的掩盖（事后看很蠢）"),
                ("internal_collapse", "内心崩溃"),
                ("resolution", "坦白/被发现/释然"),
            ],
            "key_prompts": [
                "诱惑是什么？（小但当时很珍贵）",
                "每次怎么说服自己的？",
                "掩盖方法是什么？（越蠢越好）",
                "焦虑怎么表现的？（睡不着？心跳？）",
            ],
        },
        "contradiction_reveal": {
            "name": "反差暴露",
            "sharing_urge": ["absurdity_overflow", "unfinished_processing"],
            "abnormality": ["identity"],
            "structure": [
                ("trigger", "分享欲触发：被问到/自己想到"),
                ("surface", "表面的说法"),
                ("proof", "自我论证：为什么我适合/想要"),
                ("but", "转折：'但其实...'"),
                ("contradiction", "矛盾展示"),
                ("example", "具体例子证明"),
                ("give_up", "放弃解释，自嘲"),
            ],
            "key_prompts": [
                "表面说的是什么？",
                "真实想法是什么？",
                "有什么具体例子能证明这个矛盾？",
            ],
        },
        "kindness_trap": {
            "name": "善意困境",
            "sharing_urge": ["unfinished_processing", "sensory_trigger"],
            "abnormality": ["reaction"],
            "structure": [
                ("trigger", "分享欲触发：那件事一直在脑子里"),
                ("kindness", "收到善意（具体、小、意外）"),
                ("chaos", "内心混乱（多种情绪）"),
                ("awkward", "笨拙的反应"),
                ("lingering", "事后一直在想"),
                ("admission", "承认'我好像有点问题'"),
            ],
            "key_prompts": [
                "具体是什么善意？（要小）",
                "当时脑子里在想什么？（多种声音）",
                "实际做了什么？（越笨拙越好）",
                "为什么这件事一直记得？",
            ],
        },
        "anger_armor": {
            "name": "愤怒掩护",
            "sharing_urge": ["pain_point_hit"],
            "abnormality": ["perception"],
            "structure": [
                ("trigger", "触发点：别人说了什么"),
                ("explosion", "愤怒爆发"),
                ("truth", "愤怒背后的真相"),
                ("wound", "更深的脆弱"),
                ("armor_back", "用愤怒收尾"),
            ],
            "key_prompts": [
                "什么话会触发你？（对别人是小事）",
                "为什么这句话伤人？",
                "背后是什么经历/创伤？",
            ],
        },
        "tiny_shame": {
            "name": "微小羞耻",
            "sharing_urge": ["absurdity_overflow"],
            "abnormality": ["outcome", "reaction"],
            "structure": [
                ("trigger", "分享欲：太离谱了必须说"),
                ("setup", "场景设置（快）"),
                ("action", "我的行为/话"),
                ("punch", "对方的反应（punch line）"),
                ("reaction", "我的反应"),
                ("aftermath", "事后评价"),
            ],
            "key_prompts": [
                "场景是什么？",
                "punch line是什么？（要意外、精准）",
                "你的反应是什么？（越夸张越好）",
            ],
        },
        "choice_cost": {
            "name": "选择代价",
            "sharing_urge": ["secret_confession", "pain_point_hit"],
            "abnormality": ["perception"],
            "structure": [
                ("trigger", "分享欲：想坦白/被误解"),
                ("fact", "事实陈述"),
                ("assumed", "别人以为的原因"),
                ("real", "真实原因"),
                ("cost", "代价"),
                ("no_regret", "不后悔的部分"),
                ("but", "遗憾的部分"),
            ],
            "key_prompts": [
                "别人以为是什么原因？",
                "真实原因是什么？",
                "付出了什么代价？",
                "不后悔，但...？",
            ],
        },
    }

    def generate_nucleus(self, topic: str, character_config: dict = None) -> dict:
        """
        生成故事内核

        Returns:
            {
                "pattern": "slippery_slope",
                "sharing_urge": {...},
                "abnormality": "...",
                "structure": [...],
                "prompts": [...],
                "opening": "..."
            }
        """
        pattern = self._select_pattern(topic)
        nucleus = self.NUCLEUS_PATTERNS[pattern]

        urge_type = random.choice(nucleus["sharing_urge"])
        urge = self.SHARING_URGES[urge_type]
        opening = random.choice(urge["opening_templates"])

        abnormality = random.choice(nucleus["abnormality"])

        return {
            "pattern": pattern,
            "pattern_name": nucleus["name"],
            "sharing_urge": {
                "type": urge_type,
                "description": urge["description"],
                "pattern": urge["pattern"],
                "opening": opening,
            },
            "abnormality": {
                "type": abnormality,
                "description": self.ABNORMALITIES[abnormality],
            },
            "structure": nucleus["structure"],
            "key_prompts": nucleus["key_prompts"],
        }

    def _select_pattern(self, topic: str) -> str:
        """根据话题选择模式"""
        topic_lower = topic.lower()

        if any(w in topic_lower for w in ["偷", "忍不住", "控制不住", "上瘾", "一点点"]):
            return "slippery_slope"
        if any(w in topic_lower for w in ["其实", "但是", "矛盾", "表面"]):
            return "contradiction_reveal"
        if any(w in topic_lower for w in ["帮", "送", "善意", "好心"]):
            return "kindness_trap"
        if any(w in topic_lower for w in ["生气", "愤怒", "烦", "凭什么"]):
            return "anger_armor"
        if any(w in topic_lower for w in ["尴尬", "丢人", "离谱", "笑死"]):
            return "tiny_shame"
        if any(w in topic_lower for w in ["选择", "放弃", "代价", "没能"]):
            return "choice_cost"

        return random.choice(list(self.NUCLEUS_PATTERNS.keys()))
