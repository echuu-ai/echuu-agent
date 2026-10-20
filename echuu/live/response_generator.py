"""
LLM 驱动的弹幕响应生成器（基于记忆系统、人格倾向和多语言）。
使用LLM根据用户关系、历史互动、人格、语言生成个性化回应。
"""

from __future__ import annotations

import json
import re
from typing import Dict, Optional

from echuu.diagnostics import diagnostic

from .language import (
    Language,
    StreamLanguageContext,
    detect_danmaku_language,
    detect_language,
)
from .llm_client import LLMClient
from .state import Danmaku, PerformerMemory, UserProfile
from .untrusted import render_untrusted, sanitize_untrusted


class DanmakuResponseGenerator:
    """
    使用LLM生成个性化、自然的弹幕响应。
    基于用户档案、互动历史、人格倾向、语言生成回应。
    """

    RESPONSE_PROMPT = """你是正在直播的VTuber主播{name}。

## 你的人设
- 人设: {persona}
- 背景: {background}
- 说话风格: 自然口语化，像真人一样

## 语言要求
{language_hint}

## 当前情况
- 正在讲到: {stage}
- 刚才说: {current_text_preview}
- 接下来要讲: {next_text_preview}

## 收到的弹幕
用户: {username}
关系: {user_relationship}
类型: {danmaku_type}
内容:
{danmaku_text}

{user_context}

## 你的记忆系统
你记得这些观众的特点和关系。根据关系调整回应方式：

**核心粉丝（互动20+次，有SC记录）**:
- 用亲切的称呼（可以叫昵称）
- 提到他们的偏好
- 感谢他们的支持

**老观众（互动10+次）**:
- 熟悉的语气
- 友好的回应

**眼熟的观众（互动3+次）**:
- 熟悉的称呼

**新观众**:
- 热情欢迎（用欢迎语）
- 简短回应

## 回应原则（非常重要）
1. **不要复述弹幕** - 不要把弹幕原文重新念一遍再回应
2. **自然反应** - 像真人一样
3. **根据关系调整** - 熟人更随意，新人更热情
4. **只说这一句话本身** - 不要带称呼模板、不要解释你为什么这么回

## 输出格式（纯JSON，无markdown）
{{
    "response": "<你的自然回应>",
    "action": "continue"
}}

只输出JSON，不要其他内容。"""

    def __init__(self, llm: LLMClient, stream_lang_context: Optional[StreamLanguageContext] = None):
        self.llm = llm
        self.stream_lang_context = stream_lang_context

    def generate_response(
        self,
        danmaku: Danmaku,
        current_line,
        next_line: Optional,
        memory: PerformerMemory,
        name: str,
        persona: str,
        background: str,
    ) -> Dict:
        """
        生成对弹幕的个性化响应。
        使用LLM根据用户档案、关系、历史、语言生成回应。
        """
        # 更新用户档案（自动记录互动）
        user_profile = memory.update_user_from_danmaku(danmaku)

        # 判断是否应该用欢迎消息（新观众或明显支持）
        if self.stream_lang_context and self.stream_lang_context.should_use_welcome_message(user_profile, danmaku.text):
            # 生成欢迎消息
            danmaku_lang = detect_language(danmaku.text)
            welcome_msg = danmaku_lang.get_welcome_message(danmaku.user, name)
            return {
                "response": welcome_msg,
                "action": "continue",
                "next_content": "",
            }

        # 判断弹幕类型
        danmaku_type = self._classify_danmaku(danmaku)

        # 获取用户上下文
        user_context = self._build_user_context(user_profile, memory)

        # 获取关系描述
        user_relationship = user_profile.get_bonding_description()

        # 获取语言提示
        language_hint = "用中文回应，自然口语化，像真人一样"
        if self.stream_lang_context:
            language_hint = self.stream_lang_context.get_language_hint_for_llm(danmaku.text)

        # 构建prompt
        prompt = self.RESPONSE_PROMPT.format(
            name=name,
            persona=persona,
            background=background,
            stage=current_line.stage,
            current_text_preview=current_line.text[:60] + "...",
            next_text_preview=next_line.text[:60] + "..." if next_line else "（故事即将结束）",
            # 观众可控字段：用户名和弹幕正文都要去结构、限长
            username=sanitize_untrusted(danmaku.user, max_chars=24),
            user_relationship=user_relationship,
            danmaku_text=render_untrusted(danmaku.text),
            danmaku_type=danmaku_type,
            user_context=user_context,
            language_hint=language_hint,
        )

        try:
            response_text = self.llm.call(
                system="你是一个VTuber主播，正在直播。你记得你的观众，会根据关系不同而回应。用JSON格式回复。",
                prompt=prompt,
                max_tokens=400,
            )

            # 清理和解析响应
            response_text = self._clean_json_response(response_text)
            result = json.loads(response_text)

            # 确保必要字段存在
            if "response" not in result or not result["response"]:
                # Fallback: 根据关系生成简单回应
                result = {
                    "response": self._generate_fallback_response(user_profile, danmaku),
                    "action": "continue"
                }

            result.setdefault("action", "continue")
            result.setdefault("next_content", "")

            return result

        except json.JSONDecodeError as exc:
            diagnostic(f"[DanmakuResponse] JSON解析失败: {exc}")
            diagnostic(f"[DanmakuResponse] 原始响应: {response_text[:200]}")
            # 尝试提取response字段
            extracted = self._try_extract_response(response_text)
            if extracted:
                return {
                    "response": extracted,
                    "action": "continue",
                    "next_content": "",
                }
            # 使用智能fallback
            return {
                "response": self._generate_fallback_response(user_profile, danmaku),
                "action": "continue",
                "next_content": "",
            }
        except Exception as exc:
            diagnostic(f"[DanmakuResponse] LLM调用失败: {exc}")
            return {
                "response": self._generate_fallback_response(user_profile, danmaku),
                "action": "continue",
                "next_content": "",
            }

    def _classify_danmaku(self, danmaku: Danmaku) -> str:
        """分类弹幕类型。"""
        if danmaku.is_sc:
            return f"SC打赏 (¥{danmaku.amount}) - 感谢支持"
        elif danmaku.is_question():
            return "问题 - 想知道更多信息"
        elif any(kw in danmaku.text for kw in ["哈哈", "笑", "xswl", "233", "hhh", "www"]):
            return "情绪反应 - 觉得好笑"
        elif any(kw in danmaku.text for kw in ["对", "是", "没错", "确实", "确实"]):
            return "认同 - 表示同意"
        elif any(kw in danmaku.text for kw in ["然后", "接下来", "之后", "结局", "呢"]):
            return "追问 - 想知道后续"
        elif any(kw in danmaku.text for kw in ["加油", "冲", "支持", "棒", "好"]):
            return "鼓励 - 给予支持"
        else:
            return "普通评论"

    def _build_user_context(self, user: UserProfile, memory: PerformerMemory) -> str:
        """构建用户上下文信息。"""
        contexts = []

        # 基本互动信息
        contexts.append(f"互动次数: {user.interaction_count}")

        # 反应风格
        if user.reaction_style:
            contexts.append(f"反应风格: {user.reaction_style}")

        # SC历史
        if user.total_sc_amount > 0:
            contexts.append(f"累计打赏: ¥{user.total_sc_amount}")

        # 特殊时刻
        if user.special_moments:
            contexts.append(f"特殊时刻: {', '.join(user.special_moments[-2:])}")

        # 其他活跃观众（用于群体感）
        active_users = memory.get_active_users_context(limit=3)
        if active_users and active_users != "当前直播间主要是新观众":
            contexts.append(f"\n{active_users}")

        return "\n".join(contexts) if contexts else "新观众，暂无历史记录"

    def _generate_fallback_response(self, user: UserProfile, danmaku: Danmaku) -> str:
        """
        生成智能fallback回应（根据关系和弹幕类型）。
        这是LLM失败时的备用方案，但仍基于用户关系。
        """
        # 根据关系级别和弹幕类型生成回应
        bonding = user.bonding_level

        # SC打赏 - 优先感谢
        if danmaku.is_sc:
            if bonding >= 3:
                return f"感谢老板！又来支持我了"
            elif bonding >= 2:
                return f"谢谢老板的SC！"
            else:
                return f"哇感谢{user.username}的SC！"

        # 问题
        if danmaku.is_question():
            if bonding >= 2:
                return f"别急别急，马上就说"
            else:
                return f"好嘞，听我慢慢说"

        # 笑话/幽默反应
        if any(kw in danmaku.text for kw in ["哈哈", "笑", "xswl"]):
            if bonding >= 3:
                return f"{user.username}你每次都笑这么开心"
            elif bonding >= 2:
                return f"哈哈哈哈"
            else:
                return f"哎你们别笑啊"

        # 加油/鼓励
        if any(kw in danmaku.text for kw in ["加油", "冲", "支持"]):
            if bonding >= 2:
                return f"好嘞好嘞"
            else:
                return f"谢谢谢谢！"

        # 默认回应
        if bonding >= 3:
            return f"{user.username}你又来了"
        elif bonding >= 2:
            return f"诶你又来了"
        elif bonding >= 1:
            return f"好嘞"
        else:
            return f"哦对"

    def _clean_json_response(self, response_text: str) -> str:
        """清理LLM响应，提取JSON内容。"""
        # 移除markdown标记
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        # 移除注释
        lines = response_text.split('\n')
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('//'):
                continue
            cleaned_lines.append(line)

        response_text = '\n'.join(cleaned_lines).strip()

        # 修复常见JSON问题
        response_text = re.sub(r',\s*}', '}', response_text)
        response_text = re.sub(r',\s*]', ']', response_text)

        return response_text

    def _try_extract_response(self, response_text: str) -> Optional[str]:
        """尝试从截断的JSON中提取response字段。"""
        try:
            match = re.search(r'"response"\s*:\s*"([^"]*)', response_text)
            if match:
                extracted = match.group(1)
                # 检查是否被截断
                if not extracted.endswith('"'):
                    return None  # 被截断了，返回None让fallback处理
                return extracted
        except:
            pass
        return None

    def generate_quick_response(self, danmaku: Danmaku, user: UserProfile) -> str:
        """快速生成简单回应（不调用LLM，用于紧急情况）。"""
        return self._generate_fallback_response(user, danmaku)
