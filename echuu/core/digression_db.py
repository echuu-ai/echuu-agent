"""
跑题素材库 - 真正的跑题，不是为铺垫服务的假跑题

关键洞察：真实跑题是"联想链"
- 说到钱 → 想到汇率 → 感慨汇率变化 → 忘了在说什么
- 说到住处 → 想到几楼 → 想到没电梯 → 跑远了
"""

import random
import re
from typing import Optional

# 拉回只用短钩子，绝不把策划写的 topic 整句念出来。
_RETURN_HOOKS = ("刚才那事", "正说的这茬", "刚才说到的")

_STOCK_ASIDES = (
    ("严重跑题一下哈……", ""),
    ("严重跑题一下哈", ""),
    ("严重跑题一下——", ""),
    ("严重跑题一下", ""),
    ("好了不说了，下一个话题", "好了就这样"),
    ("下一个话题", "弹幕那边"),
    ("内心戏？", ""),
    ("失控？", ""),
    ("对说回", "对，"),
)


def topic_return_hook(topic: str = "") -> str:
    """Audience-facing pull-back. Never echo the full topic brief."""
    return random.choice(_RETURN_HOOKS)


def scrub_recited_topic(text: str, topic: str) -> str:
    """If the model pasted the topic title, swap it for a short hook."""
    if not text:
        return text
    cleaned = text
    brief = (topic or "").strip()
    hook = topic_return_hook(brief)
    if len(brief) >= 6:
        # A topic can also be an ordinary factual phrase inside a sentence.
        # Only shorten explicit title recitals; never erase that phrase globally.
        recital = re.compile(r"((?:说回|回到|今天的主题是|本次主题是)\s*)" + re.escape(brief))
        cleaned = recital.sub(lambda match: match.group(1) + hook, cleaned)
    for src, dst in _STOCK_ASIDES:
        cleaned = cleaned.replace(src, dst)
    cleaned = re.sub(r"[。．]{2,}", "。", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


class DigressionDB:

    CHAINS = {
        "money_chain": {
            "triggers": ["钱", "贵", "便宜", "买", "花", "多少钱", "日元", "块钱"],
            "templates_zh": [
                "那时候{currency}多少来着...{guess1}还是{guess2}？反正比现在{comparison}，诶对，{return_topic}",
                "多少钱来着...{guess1}？不对{guess2}吧，挺{cost_adj}的——对，{return_topic}",
            ],
            "variables": {
                "currency": ["汇率", "那边的物价"],
                "guess1": ["六块几", "七块", "几百"],
                "guess2": ["七块多", "六块八", "四五百"],
                "comparison": ["贵", "高"],
                "current": ["四块八了", "五块不到", "涨了一倍"],
                "cost_adj": ["贵", "便宜", "离谱"],
                "cost_context": ["穷得要死", "根本买不起", "天天算着花"],
            },
            "min_length": 50,
        },
        "location_chain": {
            "triggers": ["住", "家", "房子", "公寓", "宿舍", "楼"],
            "templates_zh": [
                "那时候我住{location}那边，{location_detail}，租金{rent_guess}来着——对，{return_topic}",
                "我那个房子{floor_detail}，每天{daily_pain}，不说这个了，{return_topic}",
            ],
            "variables": {
                "location": ["市中心", "郊区", "学校旁边", "那条街"],
                "location_detail": ["很吵的那条街", "楼下有便利店的", "窗户朝北的那个"],
                "location_memory": ["楼下每天早上六点就有人在叫卖", "隔壁老是装修", "夏天热死冬天冷死"],
                "rent_guess": ["八百还是九百", "一千出头吧", "几百块"],
                "rent_comment": ["对我来说就是天价", "比现在便宜多了", "每个月交完就没钱了"],
                "floor_detail": ["三楼没电梯", "在顶楼", "地下室"],
                "daily_pain": ["爬上去都要死", "热得睡不着", "潮得发霉"],
                "location_tangent": ["我那个邻居还特别奇怪，总是半夜...算了不说了"],
            },
            "min_length": 60,
        },
        "time_chain": {
            "triggers": ["那时候", "以前", "当时", "那年", "那会儿"],
            "templates_zh": [
                "那是{year}年吧...还是{year_alt}？反正{vague_time}，都{time_ago}了——说什么来着？{return_topic}",
            ],
            "variables": {
                "year": ["19", "20", "18", "17"],
                "year_alt": ["20", "19", "21", "18"],
                "vague_time": ["冬天", "刚开学", "快毕业", "过年前后"],
                "time_context": ["那时候我还在上学", "那会儿我刚工作", "我记得那时候疫情刚开始"],
                "time_ago": ["五六年", "好几年", "快十年"],
            },
            "min_length": 40,
        },
        "food_chain": {
            "triggers": ["吃", "饿", "好吃", "餐厅", "外卖", "饭"],
            "templates_zh": [
                "说到吃的，那时候常吃{food}，{food_detail}，{price_guess}——跑题了，{return_topic}",
            ],
            "variables": {
                "food": ["泡面", "便利店饭团", "路边摊", "食堂"],
                "food_detail": ["最便宜的那种", "打折的时候买的", "难吃但是便宜"],
                "price_guess": ["几块钱", "十块不到", "比外面便宜一半"],
                "food_tangent": ["现在那家店都关了", "后来涨价了就不去了", "想想那时候真能省"],
            },
            "min_length": 45,
        },
        "person_chain": {
            "triggers": ["他", "她", "室友", "朋友", "同学", "同事"],
            "templates_zh": [
                "说到{person}，人{trait}，有一次{anecdote}——算了，{return_topic}",
            ],
            "variables": {
                "person": ["我室友", "我那个朋友", "我同学"],
                "trait": ["特别搞笑", "人特别好", "有点奇怪但是不讨厌"],
                "anecdote": ["半夜起来吃东西被我抓到", "借我钱从来不催", "说了一句话把我笑死"],
                "return_summary": ["就是这么个人", "人还不错"],
            },
            "min_length": 50,
        },
    }

    RETURN_MARKERS_ZH = [
        "诶我说到哪了？对，{topic}",
        "跑题了，继续说{topic}",
        "不说这个了，{topic}",
        "算了，说回{topic}",
        "...对，{topic}",
    ]

    RETURN_MARKERS_EN = [
        "wait where was I? right, {topic}",
        "anyway, back to {topic}",
        "got sidetracked, so {topic}",
        "anyway about {topic}",
    ]

    def find_injection_point(self, text: str) -> Optional[dict]:
        """
        在文本中找到可以注入跑题的位置

        Returns:
            {
                "position": 文本中的位置,
                "trigger_word": 触发词,
                "chain_type": 匹配的跑题链类型
            }
        """
        for chain_type, config in self.CHAINS.items():
            for trigger in config["triggers"]:
                if trigger in text:
                    pos = text.find(trigger) + len(trigger)
                    next_punct = len(text)
                    for punct in ["。", "，", "！", "？", "...", ",", ".", "!"]:
                        p = text.find(punct, pos)
                        if p != -1 and p < next_punct:
                            next_punct = p

                    return {
                        "position": next_punct,
                        "trigger_word": trigger,
                        "chain_type": chain_type,
                    }
        return None

    def generate_digression(
        self, chain_type: str, return_topic: str, language: str = "zh", character_config: dict = None
    ) -> str:
        """
        生成一段跑题内容

        Args:
            chain_type: 跑题链类型
            return_topic: 要拉回的话题关键词
            language: 语言
            character_config: 角色配置（可用于个性化）

        Returns:
            生成的跑题文本，包含拉回标记
        """
        config = self.CHAINS.get(chain_type)
        if not config:
            return ""

        template_key = f"templates_{language}"
        templates = config.get(template_key, config.get("templates_zh", []))
        template = random.choice(templates)

        result = template
        for var_name, options in config.get("variables", {}).items():
            if f"{{{var_name}}}" in result:
                result = result.replace(f"{{{var_name}}}", random.choice(options))

        result = result.replace("{return_topic}", topic_return_hook(return_topic))

        return result

    def inject_digression(self, script_line: str, topic: str, language: str = "zh", force: bool = False) -> tuple:
        """
        在脚本行中注入跑题

        Args:
            script_line: 原始台词
            topic: 当前话题（用于拉回）
            language: 语言
            force: 是否强制注入（即使没找到触发词）

        Returns:
            (new_text, injection_info)
        """
        injection_point = self.find_injection_point(script_line)

        if not injection_point and not force:
            return script_line, None

        if not injection_point and force:
            mid = len(script_line) // 2
            for i in range(mid, len(script_line)):
                if script_line[i] in "，。,. ":
                    mid = i + 1
                    break

            digression = self.generate_digression(random.choice(list(self.CHAINS.keys())), topic, language)

            new_text = script_line[:mid] + "..." + digression + script_line[mid:]
            return new_text, {"type": "forced", "position": mid}

        digression = self.generate_digression(injection_point["chain_type"], topic, language)

        pos = injection_point["position"]
        new_text = script_line[:pos] + "..." + digression + script_line[pos:]

        return new_text, injection_point
