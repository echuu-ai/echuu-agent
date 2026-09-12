"""
运行时状态与数据结构定义。
Enhanced with user memory and bonding system.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, TYPE_CHECKING
from collections import defaultdict

if TYPE_CHECKING:
    from echuu.core.unit import Show


@dataclass
class Danmaku:
    """弹幕 / 投喂（带优先级与 Cursor 式排队状态）。"""

    text: str
    user: str = "观众"
    is_sc: bool = False
    amount: int = 0

    relevance: float = 0.0
    priority: float = 0.0

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: str = "chat"  # chat | gift | tangent | collect
    entities: list = field(default_factory=list)
    gift_id: str = ""
    client_id: str = ""
    status: str = "queued"  # queued | processing | applied | replied | dropped
    # Roadmap item #2 (docs/research/2026-09-12-harness-and-vtuber-roadmap.md):
    # 及时性硬指标 — monotonic clock, not wall time, so it's immune to system
    # clock adjustments during a long-running stream.
    created_at: float = field(default_factory=time.monotonic)

    @classmethod
    def from_text(cls, text: str, user: str = "观众") -> "Danmaku":
        """解析弹幕。"""
        is_sc = False
        amount = 0

        if "SC" in text or "¥" in text or "$" in text:
            is_sc = True
            import re

            match = re.search(r"[¥$]?\s*(\d+)", text)
            if match:
                amount = int(match.group(1))

        return cls(text=text, user=user, is_sc=is_sc, amount=amount)

    @classmethod
    def from_input(
        cls,
        text: str,
        user: str = "观众",
        *,
        kind: str = "chat",
        gift_id: str = "",
        client_id: str = "",
        amount: int = 0,
        entities: list | None = None,
    ) -> "Danmaku":
        """观众触发：聊天、投喂或跑毛卡，进入同一条 steering 队列。"""
        parsed = cls.from_text(text, user=user)
        parsed.kind = kind if kind in ("chat", "gift", "tangent", "collect") else "chat"
        parsed.gift_id = gift_id or ""
        parsed.client_id = client_id or ""
        parsed.entities = list(entities or [])
        if parsed.kind == "gift":
            parsed.is_sc = True
            parsed.amount = amount or parsed.amount or 10
        elif amount > 0:
            parsed.amount = amount
            parsed.is_sc = True
        return parsed

    def to_public(self) -> Dict:
        """WS / REST 上给前端的排队状态。"""
        return {
            "id": self.id,
            "client_id": self.client_id,
            "kind": self.kind,
            "gift_id": self.gift_id,
            "text": self.text,
            "user": self.user,
            "status": self.status,
            "is_sc": self.is_sc,
            "amount": self.amount,
            "entities": self.entities,
        }

    def is_question(self) -> bool:
        """判断是否是问题。"""
        return "?" in self.text or "？" in self.text


@dataclass
class UserProfile:
    """
    用户档案 - 记住每个观众的特点和偏好。
    用于建立情感连接和个性化互动。
    """
    username: str

    # 互动统计
    interaction_count: int = 0  # 总互动次数
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    # 用户偏好（通过弹幕学习）
    preferred_topics: List[str] = field(default_factory=list)  # 经常聊什么
    reaction_style: str = ""  # "幽默型" "认真提问型" "气氛组" "潜水型"
    favorite_phrases: List[str] = field(default_factory=list)  # 口头禅

    # 情感连接
    bonding_level: int = 0  # 0=陌生人 1=眼熟 2=老观众 3=核心粉丝
    special_moments: List[str] = field(default_factory=list)  # 特殊时刻（SC、难忘互动）

    # SC打赏记录
    total_sc_amount: int = 0
    sc_history: List[Dict] = field(default_factory=list)

    def update_interaction(self, danmaku: Danmaku) -> None:
        """更新用户互动记录。"""
        self.interaction_count += 1
        self.last_seen = datetime.now()
        if self.first_seen is None:
            self.first_seen = datetime.now()

        # 更新情感连接
        if self.interaction_count >= 20:
            self.bonding_level = 3  # 核心粉丝
        elif self.interaction_count >= 10:
            self.bonding_level = 2  # 老观众
        elif self.interaction_count >= 3:
            self.bonding_level = 1  # 眼熟

        # 记录SC
        if danmaku.is_sc and danmaku.amount > 0:
            self.total_sc_amount += danmaku.amount
            self.sc_history.append({
                "amount": danmaku.amount,
                "time": datetime.now().isoformat(),
                "message": danmaku.text
            })
            self.special_moments.append(f"打赏了¥{danmaku.amount}")

    def get_bonding_description(self) -> str:
        """获取关系描述（用于LLM上下文）。"""
        if self.bonding_level >= 3:
            return f"核心粉丝（互动{self.interaction_count}次，累计打赏¥{self.total_sc_amount}）"
        elif self.bonding_level >= 2:
            return f"老观众（互动{self.interaction_count}次）"
        elif self.bonding_level >= 1:
            return f"眼熟的观众（互动{self.interaction_count}次）"
        else:
            return "新观众"

    def get_context_summary(self) -> str:
        """生成用户上下文摘要（供LLM使用）。"""
        parts = [f"{self.username}: {self.get_bonding_description()}"]

        if self.reaction_style:
            parts.append(f"风格={self.reaction_style}")

        if self.preferred_topics:
            parts.append(f"关注话题={', '.join(self.preferred_topics[-3:])}")

        if self.special_moments:
            parts.append(f"特殊时刻={', '.join(self.special_moments[-2:])}")

        return " | ".join(parts)


@dataclass
class PerformerMemory:
    """记忆系统 - 可视化展示AI记住了什么。Enhanced with user profiles."""

    script_progress: Dict = field(
        default_factory=lambda: {
            "current_line": 0,
            "total_lines": 0,
            "completed_stages": [],
            "current_stage": "Hook",
        }
    )

    danmaku_memory: Dict = field(
        default_factory=lambda: {
            "received": [],
            "responded": [],
            "ignored": [],
            "pending_questions": [],
        }
    )

    # 新增：用户档案系统
    user_profiles: Dict[str, UserProfile] = field(default_factory=dict)

    promises: List[Dict] = field(default_factory=list)
    story_points: Dict = field(
        default_factory=lambda: {
            "mentioned": [],
            "upcoming": [],
            "revealed": [],
        }
    )
    emotion_track: List[Dict] = field(default_factory=list)

    def get_or_create_user(self, username: str) -> UserProfile:
        """获取或创建用户档案。"""
        if username not in self.user_profiles:
            self.user_profiles[username] = UserProfile(username=username)
        return self.user_profiles[username]

    def update_user_from_danmaku(self, danmaku: Danmaku) -> UserProfile:
        """根据弹幕更新用户档案。"""
        user = self.get_or_create_user(danmaku.user)
        user.update_interaction(danmaku)

        # 分析用户反应风格（简单启发式）
        if any(kw in danmaku.text for kw in ["哈哈", "笑", "xswl", "233"]):
            if "幽默" not in user.reaction_style:
                user.reaction_style = "幽默型"
        elif danmaku.is_question():
            if "提问" not in user.reaction_style:
                user.reaction_style = "认真提问型"
        elif any(kw in danmaku.text for kw in ["加油", "冲", "支持"]):
            if "气氛" not in user.reaction_style:
                user.reaction_style = "气氛组"

        return user

    def get_active_users_context(self, limit: int = 5) -> str:
        """
        获取活跃用户上下文（用于LLM）。
        返回最近互动的用户及其关系。
        """
        # 按最后互动时间排序
        active_users = sorted(
            [
                u for u in self.user_profiles.values()
                if u.last_seen is not None
            ],
            key=lambda u: u.last_seen,
            reverse=True
        )[:limit]

        if not active_users:
            return "当前直播间主要是新观众"

        contexts = [u.get_context_summary() for u in active_users]
        return "活跃用户: " + " | ".join(contexts)

    def get_relevant_users_for_danmaku(self, danmaku: Danmaku) -> List[UserProfile]:
        """
        获取与当前弹幕相关的用户（用于个性化回应）。
        包括：发弹幕的用户、最近活跃的用户。
        """
        relevant = [self.get_or_create_user(danmaku.user)]

        # 添加最近互动的高频用户
        for user in self.user_profiles.values():
            if user.bonding_level >= 2 and user.username != danmaku.user:
                relevant.append(user)
                if len(relevant) >= 3:
                    break

        return relevant

    def to_display(self) -> str:
        """生成用户可见的记忆状态。"""
        lines = []
        lines.append("+-------------------------------------------+")
        lines.append("| AI 记忆状态                               |")
        lines.append("+-------------------------------------------+")

        prog = self.script_progress
        current = prog.get("current_line", 0)
        total = prog.get("total_lines", 0)
        if total > 0:
            percent = int(current / total * 10)
            bar = "#" * percent + "-" * (10 - percent)
            lines.append(f"| 剧本: [{bar}] {current}/{total} ({prog.get('current_stage', '?')}) |")

        dm = self.danmaku_memory
        responded = len(dm.get("responded", []))
        pending = len(dm.get("pending_questions", []))
        lines.append(f"| 弹幕: 已回应{responded}条, 待回答{pending}个问题           |")

        # 新增：显示记住的观众
        if self.user_profiles:
            regular_viewers = [
                u for u in self.user_profiles.values()
                if u.bonding_level >= 2
            ]
            if regular_viewers:
                lines.append(f"| 熟悉观众: {len(regular_viewers)}人                        |")
                for user in regular_viewers[:2]:
                    lines.append(f"|   - {user.username} ({user.interaction_count}次互动)           |")

        unfulfilled = [p for p in self.promises if not p.get("fulfilled", False)]
        if unfulfilled:
            lines.append("| 待兑现承诺:                               |")
            for p in unfulfilled[:2]:
                content = p.get("content", "")[:20]
                lines.append(f"|   - {content}...                          |")

        if self.emotion_track:
            lines.append("| 情绪轨迹:                                 |")
            for emo in self.emotion_track[-2:]:
                level_name = {1: "微破防", 2: "明显破防", 3: "完全破防"}.get(
                    emo.get("level", 0), "?"
                )
                trigger = emo.get("trigger", "")[:12]
                lines.append(f"|   {level_name}: {trigger}...               |")

        mentioned = self.story_points.get("mentioned", [])
        if mentioned:
            lines.append("| 已提到:                                   |")
            for point in mentioned[-3:]:
                lines.append(f"|   * {point[:20]}...                        |")

        lines.append("+-------------------------------------------+")
        return "\n".join(lines)

    def to_context(self) -> str:
        """生成给 LLM 的上下文摘要。"""
        parts = []
        prog = self.script_progress
        parts.append(
            f"剧本进度: {prog.get('current_line')}/{prog.get('total_lines')} "
            f"({prog.get('current_stage')})"
        )

        # 新增：用户上下文
        if self.user_profiles:
            parts.append(self.get_active_users_context(limit=3))

        mentioned = self.story_points.get("mentioned", [])
        if mentioned:
            parts.append(f"已提到: {', '.join(mentioned[-5:])}")

        pending = self.danmaku_memory.get("pending_questions", [])
        if pending:
            parts.append(f"待回答: {', '.join(q[:20] for q in pending[:3])}")

        unfulfilled = [p for p in self.promises if not p.get("fulfilled", False)]
        if unfulfilled:
            parts.append(f"待兑现承诺: {', '.join(p['content'][:15] for p in unfulfilled[:2])}")

        return " | ".join(parts)


@dataclass
class PerformanceState:
    """表演状态（V5: 围绕 Show 组织；V4 字段保留为兼容）。"""

    name: str
    persona: str
    background: str
    topic: str

    # V5: structured Show with 4 units
    show: "Show | None" = None
    current_unit_idx: int = 0
    current_line_in_unit: int = 0

    # V4 compat (used by PerformerV3 + legacy save_script) — populated from show.units when V5 path is used
    script_lines: List = field(default_factory=list)
    current_line_idx: int = 0

    current_step: int = 0
    memory: PerformerMemory = field(default_factory=PerformerMemory)
    danmaku_queue: List[Danmaku] = field(default_factory=list)
    catchphrases: List[str] = field(default_factory=list)
    lines_since_tangent: int = 99
    token_hunt: object | None = None
