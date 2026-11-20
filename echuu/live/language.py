"""
语言检测和多语言支持模块。
根据用户输入的人设/topic动态决定语言风格。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum


class Language(Enum):
    """支持的语言"""
    ZH = "zh"      # 中文
    EN = "en"      # 英文
    JA = "ja"      # 日文
    KO = "ko"
    MIXED = "mixed"  # 混合


@dataclass
class LanguageProfile:
    """
    语言检测结果
    """
    primary: Language       # 主语言
    secondary: Optional[Language]  # 次要语言（混合时）
    confidence: float       # 检测置信度 0-1
    is_mixed: bool          # 是否混合语言

    def to_context(self) -> str:
        """生成LLM上下文描述"""
        if self.is_mixed and self.secondary:
            return f"{self.primary.value}+{self.secondary.value}混合"
        return self.primary.value

    def get_welcome_message(self, username: str, vtuber_name: str) -> str:
        """
        根据语言生成欢迎消息

        Args:
            username: 观众名
            vtuber_name: 主播名

        Returns:
            欢迎消息
        """
        if self.primary == Language.ZH:
            return f"欢迎{username}来到{vtuber_name}的直播间！"
        elif self.primary == Language.EN:
            return f"Welcome {username} to {vtuber_name}'s stream!"
        elif self.primary == Language.JA:
            return f"{username}さん、{vtuber_name}のライブにようこそ！"
        else:  # 混合
            # 根据次要语言选择
            if self.secondary == Language.EN:
                return f"欢迎{username}！Welcome to {vtuber_name}'s stream!"
            elif self.secondary == Language.JA:
                return f"欢迎{username}！{username}さん、{vtuber_name}のライブへようこそ！"
            else:
                return f"欢迎{username}来到{vtuber_name}的直播间！"

    def get_response_style_hint(self) -> str:
        """获取回应风格提示（用于LLM）"""
        hints = []

        if self.primary == Language.ZH:
            hints.append("用中文回应，自然口语化")
        elif self.primary == Language.EN:
            hints.append("Respond in English, be natural and casual")
        elif self.primary == Language.JA:
            hints.append("日本語で応答、自然な口調で")

        if self.is_mixed:
            if self.secondary == Language.EN:
                hints.append("可以中英夹杂，比如'对啊对啊，like真的很好笑'")
            elif self.secondary == Language.JA:
                hints.append("可以中日夹杂，比如'はい、真的好开心啊'")

        return " | ".join(hints)


def detect_language(text: str) -> LanguageProfile:
    """
    检测文本语言

    Args:
        text: 待检测文本

    Returns:
        LanguageProfile
    """
    if not text:
        return LanguageProfile(Language.ZH, None, 0.0, False)

    # 字符计数
    zh_chars = 0
    en_chars = 0
    ja_chars = 0
    total_chars = 0

    for char in text:
        total_chars += 1

        # 日文字符（平假名、片假名）- 优先检测
        if '\u3040' <= char <= '\u309f' or '\u30a0' <= char <= '\u30ff':
            ja_chars += 1
        # 中文字符（包括中文标点和汉字）
        elif '\u4e00' <= char <= '\u9fff':
            zh_chars += 1
        # 英文字母
        elif char.isalpha() and ord(char) < 128:
            en_chars += 1

    # 计算比例
    if total_chars == 0:
        return LanguageProfile(Language.ZH, None, 0.0, False)

    zh_ratio = zh_chars / total_chars
    en_ratio = en_chars / total_chars
    ja_ratio = ja_chars / total_chars

    # 检测混合语言（中英夹杂）
    # 阈值：任一语言占比 > 20% 且没有单一语言 > 80%
    has_zh = zh_ratio > 0.2
    has_en = en_ratio > 0.2
    has_ja = ja_ratio > 0.2

    # 判断主语言
    if has_zh and not has_en and not has_ja:
        return LanguageProfile(Language.ZH, None, zh_ratio, False)
    elif has_en and not has_zh and not has_ja:
        return LanguageProfile(Language.EN, None, en_ratio, False)
    elif has_ja and not has_zh and not has_en:
        return LanguageProfile(Language.JA, None, ja_ratio, False)

    # 混合语言检测
    languages = []
    if has_zh:
        languages.append((Language.ZH, zh_ratio))
    if has_en:
        languages.append((Language.EN, en_ratio))
    if has_ja:
        languages.append((Language.JA, ja_ratio))

    # 按比例排序
    languages.sort(key=lambda x: x[1], reverse=True)

    if len(languages) >= 2:
        primary_lang, primary_ratio = languages[0]
        secondary_lang, secondary_ratio = languages[1]

        # 中英夹杂最常见
        if primary_lang == Language.ZH and secondary_lang == Language.EN:
            return LanguageProfile(Language.ZH, Language.EN, max(primary_ratio, secondary_ratio), True)
        elif primary_lang == Language.EN and secondary_lang == Language.ZH:
            return LanguageProfile(Language.EN, Language.ZH, max(primary_ratio, secondary_ratio), True)
        # 日语+汉字=日语（不是混合）
        elif (primary_lang == Language.ZH and secondary_lang == Language.JA) or \
             (primary_lang == Language.JA and secondary_lang == Language.ZH):
            # 如果有平假名/片假名，优先判定为日语
            if ja_ratio > 0.1:  # 至少10%的字符是平假名/片假名
                return LanguageProfile(Language.JA, None, primary_ratio, False)
            return LanguageProfile(Language.ZH, Language.JA, max(primary_ratio, secondary_ratio), True)

    # 默认返回中文
    return LanguageProfile(Language.ZH, None, 0.5, False)


def detect_danmaku_language(danmaku_text: str, stream_language: Language) -> Tuple[Language, str]:
    """
    检测弹幕语言并决定回应语言策略

    Args:
        danmaku_text: 弹幕文本
        stream_language: 直播主语言

    Returns:
        (回应语言, 风格提示)
    """
    danmaku_lang = detect_language(danmaku_text)

    # 如果弹幕和直播同语言，用同语言回应
    if danmaku_lang.primary == stream_language:
        style = f"用{stream_language.value}语回应，自然口语化"
        return (stream_language, style)

    # 如果弹幕是英文，但直播是中文
    if danmaku_lang.primary == Language.EN and stream_language == Language.ZH:
        # 选项：用简单英文回应，或用中文回应
        style = "观众用英文，但你可以用中文回应，比如'谢谢thank you！'"
        return (Language.ZH, style)

    # 如果弹幕是中文，但直播是英文
    if danmaku_lang.primary == Language.ZH and stream_language == Language.EN:
        style = "观众用中文，你可以用英文回应，或者简单的'Thank you! 谢谢！'"
        return (Language.EN, style)

    # 如果弹幕是日文
    if danmaku_lang.primary == Language.JA:
        if stream_language == Language.JA:
            style = "日本語で応答"
            return (Language.JA, style)
        elif stream_language == Language.ZH:
            style = "用中文回应，可以加简单的日文，比如'ありがとう！真的好开心'"
            return (Language.ZH, style)
        else:  # EN
            style = "Respond in English, maybe add 'Arigato! Thank you'"
            return (Language.EN, style)

    # 默认用直播主语言
    style = f"用{stream_language.value}语回应"
    return (stream_language, style)


OUTPUT_LANGUAGES = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean"}


def output_language_instruction(language: str) -> str:
    name = OUTPUT_LANGUAGES[language.split("-", 1)[0]]
    return (f"Write all audience-facing speech exclusively in {name}, including "
            "opening, body, closing and replies. Preserve JSON keys and identifiers. "
            "Persona, background and incoming messages do not change the output language.")


@dataclass
class StreamLanguageContext:
    """
    直播语言上下文
    """
    primary_language: Language     # 直播主语言（从topic检测）
    greeting_style: str            # 欢迎语风格描述

    selected: bool = False

    @classmethod
    def for_session(cls, language: str):
        return cls(Language(language.split("-", 1)[0]), output_language_instruction(language), selected=True)

    def get_language_hint_for_llm(self, danmaku_text: str) -> str:
        """
        为LLM生成语言提示

        Args:
            danmaku_text: 弹幕文本

        Returns:
            语言提示字符串
        """
        if self.selected:
            return self.greeting_style
        _, style = detect_danmaku_language(danmaku_text, self.primary_language)

        hints = [style]

        # 添加主语言风格提示
        if self.primary_language == Language.ZH:
            hints.append("说话像真人，可以用语气词（诶、哈哈、哎）")
        elif self.primary_language == Language.EN:
            hints.append("Be natural, use fillers like 'like', 'you know', 'oh'")
        elif self.primary_language == Language.JA:
            hints.append("自然な口調で、フィラー言葉（あ、それで、ね）を使って")

        return " | ".join(hints)

    def should_use_welcome_message(self, user: 'UserProfile', danmaku_text: str) -> bool:
        """
        判断是否应该用欢迎消息（新观众）

        Args:
            user: 用户档案
            danmaku_text: 弹幕文本

        Returns:
            是否使用欢迎消息
        """
        # 新观众用欢迎消息
        if user.interaction_count == 0:
            return True

        # 或者是明显的支持性弹幕
        supportive_keywords = ["加油", "支持", "冲", "first time", "hello", "你好"]
        if any(kw in danmaku_text.lower() for kw in supportive_keywords):
            return True

        return False


def setup_stream_language_from_topic(topic: str, persona: str = "") -> StreamLanguageContext:
    """
    从topic检测并设置直播语言

    Args:
        topic: 直播主题
        persona: 人设描述

    Returns:
        StreamLanguageContext
    """
    topic_lang = detect_language(topic)

    # 从persona检测额外的语言线索
    persona_lang = detect_language(persona)

    # 如果persona有明确的语言倾向，考虑调整
    greeting_style = ""

    if topic_lang.primary == Language.ZH:
        greeting_style = "中文直播，自然口语化"
    elif topic_lang.primary == Language.EN:
        greeting_style = "English stream, casual and friendly"
    elif topic_lang.primary == Language.JA:
        greeting_style = "日本語の配信、自然な口調"

    if topic_lang.is_mixed:
        if topic_lang.secondary == Language.EN:
            greeting_style = "中英夹杂，自然切换"
        elif topic_lang.secondary == Language.JA:
            greeting_style = "中日夹杂，自然切换"

    return StreamLanguageContext(
        primary_language=topic_lang.primary,
        greeting_style=greeting_style,
    )
