"""
触发模式库 - 开场不只是"弹幕问了"

触发类型分布（从30个clips统计）：
- sensory (25%): 吃到/闻到/看到什么 → 联想
- danmaku (30%): 弹幕问题/评论/SC
- environment (15%): 宠物/敲门/发现时间
- body_state (10%): 困/累/饿
- thought_drift (15%): 突然想起
- unconscious (5%): 无意识行为被发现
"""

from dataclasses import dataclass
from typing import List
import random


@dataclass
class TriggerTemplate:
    type: str
    weight: float
    templates: List[str]
    requires: List[str]  # 需要角色配置哪些字段


class TriggerBank:

    TRIGGERS = {
        "sensory": {
            "weight": 0.25,
            "templates_zh": [
                "我刚才吃那个什么来着...{food}，吃{food}的时候突然想起来",
                "诶这个{thing}好香，让我想起以前...",
                "刚刚闻到一股{smell}的味道，突然想起一个事",
                "我看着{thing}发呆，然后就想起来...",
            ],
            "templates_en": [
                "I was just eating... what was it... {food}, and it reminded me of something",
                "Oh this {thing} smells so good, it reminds me of...",
                "I just smelled {smell} and suddenly remembered...",
            ],
            "requires": ["sensory_anchors"],
        },
        "environment": {
            "weight": 0.15,
            "templates_zh": [
                "{pet_name}你别闹，我在直播呢...诶说到这个",
                "等下好像有人敲门...没事，我继续说，刚想起一个事",
                "哇已经{time}点了？说到时间我想起...",
                "外面好吵，又在装修...算了不管了，我跟你们说个事",
            ],
            "templates_en": [
                "{pet_name} stop it, I'm streaming... anyway speaking of which",
                "Wait someone's at the door... nevermind, anyway I just remembered",
                "Oh it's already {time}? Speaking of time...",
            ],
            "requires": ["pet_name"],
        },
        "body_state": {
            "weight": 0.10,
            "templates_zh": [
                "我今天好累啊...对了说到累，我想起以前有一次",
                "饿死了，等会下播要去吃东西，说到吃的...",
                "困了困了...但是我突然想起一个事必须跟你们说",
                "嗓子有点不舒服...诶我之前也这样过，那次是",
            ],
            "templates_en": [
                "I'm so tired today... speaking of tired, I remember one time",
                "I'm so hungry, gonna eat after stream... speaking of food...",
                "So sleepy... but I suddenly remembered something I have to tell you",
            ],
            "requires": [],
        },
        "thought_drift": {
            "weight": 0.15,
            "templates_zh": [
                "诶我突然想起来一个事",
                "说起{keyword}我想到...",
                "不知道为什么我突然想起以前的一个事",
                "刚刚弹幕说{keyword}，让我想起...",
            ],
            "templates_en": [
                "Oh I just remembered something",
                "Speaking of {keyword}, that reminds me...",
                "I don't know why but I suddenly thought of something",
            ],
            "requires": [],
        },
        "danmaku": {
            "weight": 0.30,
            "templates_zh": [
                "弹幕问{question}...这个让我想起",
                "谢谢{user}的SC，「{content}」嘛...这个我有个故事",
                "有人说{comment}，我要反驳一下，之前有一次...",
                "「{danmaku_text}」哈哈这个弹幕，让我想起",
            ],
            "templates_en": [
                "Chat's asking {question}... this reminds me of",
                "Thanks {user} for the SC, '{content}'... I have a story about this",
                "Someone said {comment}, I gotta push back on that, one time...",
            ],
            "requires": ["danmaku_content"],
        },
        "unconscious": {
            "weight": 0.05,
            "templates_zh": [
                "诶？我刚才在干嘛...你们别说我知道，我在{action}",
                "啊被你们发现了，我刚刚在{action}，这个习惯是因为...",
                "等等我刚说了什么？算了，继续",
            ],
            "templates_en": [
                "Huh? What was I doing... don't tell me, I know, I was {action}",
                "Ah you caught me, I was {action}, this habit is from...",
            ],
            "requires": ["unconscious_habits"],
        },
    }

    def sample(self, character_config: dict, language: str = "zh", context: dict = None) -> dict:
        """
        采样一个触发方式

        Args:
            character_config: 角色配置，包含 sensory_anchors, pet_name 等
            language: "zh" 或 "en"
            context: 可选的上下文（如当前弹幕）

        Returns:
            {
                "type": "sensory",
                "template": "我刚才吃那个什么来着...{food}，吃{food}的时候突然想起来",
                "filled": "我刚才吃那个什么来着...<character_config.sensory_anchors.food>，吃<...>的时候突然想起来",
                "variables_used": {"food": "<取自 character_config.sensory_anchors.food，不预置具体人物经历>"}
            }
        """
        available_triggers = []
        weights = []

        for trigger_type, config in self.TRIGGERS.items():
            can_use = True
            for req in config["requires"]:
                if req not in character_config or not character_config[req]:
                    can_use = False
                    break

            if can_use:
                available_triggers.append(trigger_type)
                weights.append(config["weight"])

        total = sum(weights)
        weights = [w / total for w in weights]

        chosen_type = random.choices(available_triggers, weights=weights, k=1)[0]
        config = self.TRIGGERS[chosen_type]

        template_key = f"templates_{language}"
        templates = config.get(template_key, config.get("templates_zh", []))
        template = random.choice(templates)

        filled, variables = self._fill_template(template, character_config, context)

        return {
            "type": chosen_type,
            "template": template,
            "filled": filled,
            "variables_used": variables,
        }

    def _fill_template(self, template: str, config: dict, context: dict = None) -> tuple:
        """填充模板变量"""
        variables = {}
        filled = template

        if "{food}" in template and "sensory_anchors" in config:
            food = random.choice(config["sensory_anchors"].get("food", ["东西"]))
            filled = filled.replace("{food}", food)
            variables["food"] = food

        if "{thing}" in template and "sensory_anchors" in config:
            thing = random.choice(config["sensory_anchors"].get("things", ["这个"]))
            filled = filled.replace("{thing}", thing)
            variables["thing"] = thing

        if "{smell}" in template and "sensory_anchors" in config:
            smell = random.choice(config["sensory_anchors"].get("smells", ["奇怪"]))
            filled = filled.replace("{smell}", smell)
            variables["smell"] = smell

        if "{pet_name}" in template:
            pet_name = config.get("pet_name", "喵喵")
            filled = filled.replace("{pet_name}", pet_name)
            variables["pet_name"] = pet_name

        if "{time}" in template:
            import datetime

            hour = datetime.datetime.now().hour
            filled = filled.replace("{time}", str(hour))
            variables["time"] = str(hour)

        if context:
            for key in ["question", "user", "content", "comment", "danmaku_text", "keyword"]:
                if f"{{{key}}}" in template and key in context:
                    filled = filled.replace(f"{{{key}}}", context[key])
                    variables[key] = context[key]

        if "{action}" in template and "unconscious_habits" in config:
            action = random.choice(config.get("unconscious_habits", ["发呆"]))
            filled = filled.replace("{action}", action)
            variables["action"] = action

        return filled, variables
