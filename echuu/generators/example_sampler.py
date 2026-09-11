"""
ExampleSampler - Few-shot 随机采样器。
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Dict, Iterable, List


class ExampleSampler:
    """
    从真实 VTuber 片段中随机采样 few-shot examples。
    """

    def __init__(self, clips_path: str):
        self.clips: List[Dict] = []
        clips_file = Path(clips_path)

        if clips_file.exists():
            with clips_file.open("r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.clips.append(json.loads(line))
            print(f"ExampleSampler: 加载了 {len(self.clips)} 个 clips")
        else:
            print(f"clips 文件不存在: {clips_path}")

        self.categorize_clips()

    def categorize_clips(self):
        """将 clips 按特征分类。"""
        self.by_emotion = {
            "high_emotion": [],
            "casual": [],
            "storytelling": [],
        }

        self.by_structure = {
            "linear": [],
            "digressive": [],
            "interactive": [],
        }

        zh_high_emotion = ["爆发", "紧张", "释然", "感动", "破防", "愤怒", "震惊", "激动"]
        zh_casual = ["轻松", "调侃", "自嘲", "搞笑", "宠溺"]

        en_high_emotion = ["angry", "rage", "crying", "emotional", "sad", "vulnerable", "break"]
        en_casual = ["funny", "chill", "relax", "joking", "sarcastic"]

        for clip in self.clips:
            notes = clip.get("notes", {})
            lang = clip.get("language", "zh")

            emotion = notes.get("emotion", "").lower()
            feature = notes.get("feature", "").lower()

            high_markers = zh_high_emotion if lang == "zh" else en_high_emotion
            casual_markers = zh_casual if lang == "zh" else en_casual

            is_high = any(word in emotion or word in feature for word in high_markers)
            is_casual = any(word in emotion or word in feature for word in casual_markers)

            if "**" in feature:
                is_high = True

            if is_high:
                self.by_emotion["high_emotion"].append(clip)
            elif is_casual:
                self.by_emotion["casual"].append(clip)
            else:
                self.by_emotion["storytelling"].append(clip)

            structure = notes.get("structure", "")
            if "跑题" in structure or "插入" in structure or "digress" in structure.lower():
                self.by_structure["digressive"].append(clip)
            elif "互动" in structure or "弹幕" in structure or "interactive" in structure.lower():
                self.by_structure["interactive"].append(clip)
            else:
                self.by_structure["linear"].append(clip)

        print(
            "   情绪分类: 高情绪={}, 日常={}, 叙事={}".format(
                len(self.by_emotion["high_emotion"]),
                len(self.by_emotion["casual"]),
                len(self.by_emotion["storytelling"]),
            )
        )
        print(
            "   结构分类: 跑题={}, 互动={}, 线性={}".format(
                len(self.by_structure["digressive"]),
                len(self.by_structure["interactive"]),
                len(self.by_structure["linear"]),
            )
        )

    def sample_diverse(self, n: int = 3, language: str = None) -> List[Dict]:
        """采样 n 个多样化的 examples。"""
        if language:
            available = [c for c in self.clips if c.get("language") == language]
            high_emotion = [
                c for c in self.by_emotion["high_emotion"] if c.get("language") == language
            ]
            digressive = [
                c for c in self.by_structure["digressive"] if c.get("language") == language
            ]
        else:
            available = self.clips
            high_emotion = self.by_emotion["high_emotion"]
            digressive = self.by_structure["digressive"]

        if not available:
            print(f"没有可用的 clips (language={language})")
            return []

        samples: List[Dict] = []

        if high_emotion:
            samples.append(random.choice(high_emotion))

        digressive_available = [c for c in digressive if c not in samples]
        if digressive_available:
            samples.append(random.choice(digressive_available))

        remaining = [c for c in available if c not in samples]
        while len(samples) < n and remaining:
            choice = random.choice(remaining)
            samples.append(choice)
            remaining.remove(choice)

        random.shuffle(samples)
        return samples

    # ---- V2: 真·相关性采样（修 sample_diverse 的三个根因） -------------------
    @staticmethod
    def _tokens(text: str) -> set:
        """粗分词：英文按词，中文按 2-gram + 单字，够 mockup 用。"""
        text = (text or "").lower()
        out: set = set()
        for w in re.findall(r"[a-z0-9]+", text):
            if len(w) >= 2:
                out.add(w)
        zh = re.findall(r"[一-鿿]+", text)
        for run in zh:
            out.update(run)  # 单字
            for i in range(len(run) - 1):
                out.add(run[i:i + 2])  # 2-gram
        return out

    def _clip_text(self, clip: Dict) -> str:
        notes = clip.get("notes", {}) or {}
        parts = [
            clip.get("title", ""),
            notes.get("emotion", ""),
            notes.get("feature", ""),
            notes.get("structure", ""),
            notes.get("trigger", ""),
        ]
        for seg in clip.get("transcript", [])[:6]:
            parts.append(seg.get("text", ""))
        return " ".join(p for p in parts if p)

    def _relevance(self, clip: Dict, query_tokens: set, emotion: str, language: str) -> float:
        clip_tokens = self._tokens(self._clip_text(clip))
        if not clip_tokens:
            return 0.0
        overlap = len(query_tokens & clip_tokens)
        score = overlap / (len(query_tokens) ** 0.5 + 1.0)  # 长查询不至于碾压
        # 软语言偏好（根因 A：不再硬切，只加权）
        if language and clip.get("language") == language:
            score += 0.5
        # 软情绪偏好
        if emotion:
            notes = clip.get("notes", {}) or {}
            blob = (notes.get("emotion", "") + notes.get("feature", "")).lower()
            if emotion.lower() in blob:
                score += 0.3
        return score

    def sample_relevant(
        self,
        topic: str,
        persona: str = "",
        emotion: str = None,
        n: int = 3,
        language: str = None,
        hard_language: bool = False,
        temperature: float = 0.6,
        seed: int = None,
        allowed_ids: Iterable[str] | None = None,
        exclude_ids: Iterable[str] = (),
    ) -> List[Dict]:
        """按 topic/persona/emotion 与 clip 内容的相关性加权采样。

        修三个根因：
          A. hard_language=False → 语言只作软加权，英文池(后 20 条)可达。
          B. 不依赖死掉的 by_structure["digressive"] 桶，对全池打分。
          C. 真·相关性（token 重叠），temperature 控制相关 vs 多样，seed 可复现。
        零重叠时优雅退化为近似随机（不崩）。
        """
        rng = random.Random(seed)
        allowed = set(allowed_ids) if allowed_ids is not None else None
        excluded = set(exclude_ids)
        pool = [
            clip for clip in self.clips
            if (allowed is None or clip.get("clip_id") in allowed)
            and clip.get("clip_id") not in excluded
        ]
        if hard_language and language:
            same_language = [c for c in pool if c.get("language") == language]
            pool = same_language or pool
        if not pool:
            return []

        query_tokens = self._tokens(f"{topic} {persona}")
        scored = [(self._relevance(c, query_tokens, emotion or "", language), c) for c in pool]

        # temperature=0 → 纯按相关性 top-n；>0 → 相关性为主的加权随机
        if temperature <= 0:
            scored.sort(key=lambda x: x[1] is not None)  # stable
            scored.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in scored[:n]]

        chosen: List[Dict] = []
        remaining = scored[:]
        for _ in range(min(n, len(remaining))):
            weights = [max(s, 0.0) ** (1.0 / temperature) + 0.01 for s, _ in remaining]
            total = sum(weights)
            r = rng.random() * total
            acc = 0.0
            pick = len(remaining) - 1
            for i, w in enumerate(weights):
                acc += w
                if r <= acc:
                    pick = i
                    break
            chosen.append(remaining.pop(pick)[1])
        return chosen

    def get_relevant_examples(
        self, topic: str, persona: str = "", emotion: str = None,
        n: int = 3, language: str = "zh", seed: int = None,
        allowed_ids: Iterable[str] | None = None,
        exclude_ids: Iterable[str] = (),
    ) -> str:
        """一键获取按相关性采样、格式化好的 few-shot examples。

        hard_language=True：范例教的是口语节奏和骨架，跨语言范例会把节奏带偏
        （中文场混英文切片）。sample_relevant 在该语言池为空时自动退回全池。
        """
        formatted, _ = self.get_relevant_examples_with_ids(
            topic=topic, persona=persona, emotion=emotion, n=n,
            language=language, seed=seed, allowed_ids=allowed_ids,
            exclude_ids=exclude_ids,
        )
        return formatted

    def get_relevant_examples_with_ids(
        self, topic: str, persona: str = "", emotion: str = None,
        n: int = 3, language: str = "zh", seed: int = None,
        allowed_ids: Iterable[str] | None = None,
        exclude_ids: Iterable[str] = (),
    ) -> tuple[str, list[str]]:
        """Return formatted examples and the exact clip IDs used in the prompt."""
        samples = self.sample_relevant(
            topic=topic, persona=persona, emotion=emotion, n=n,
            language=language, hard_language=True, seed=seed,
            allowed_ids=allowed_ids, exclude_ids=exclude_ids,
        )
        if not samples:
            return "（无可用的 few-shot examples）", []
        ids = [str(sample.get("clip_id", "")) for sample in samples if sample.get("clip_id")]
        return self.format_as_fewshot(samples), ids

    def extract_transcript_segments(self, clip: Dict, max_chars: int = 700) -> str:
        """按时间顺序给出完整 transcript（超长时在段边界截到 max_chars）。

        真实切片的密度分布本身就是范例：大部分是松弛铺垫，高光只占一小段。
        只抽"最有戏"的几句会把铺垫全过滤掉，few-shot 被偏置成高光集锦，
        模型学到的就是"每句都得炸"。
        """
        transcript = clip.get("transcript", [])
        if not transcript:
            return ""

        segments = sorted(transcript, key=lambda s: s.get("t", 0) or 0)
        result = []
        total = 0
        for seg in segments:
            text = seg.get("text", "")
            if not text:
                continue
            if result and total + len(text) > max_chars:
                break
            result.append(text)
            total += len(text)
        return "\n".join(result)

    def format_as_fewshot(self, clips: List[Dict]) -> str:
        """Expose only numeric delivery features; source facts never enter a writer prompt.

        Titles, annotations and structure notes can all contain personal history,
        so none of their free text is forwarded, even with a 'do not copy' warning.
        Raw transcripts remain available to offline retrieval/evaluation only.
        """
        output = []
        for i, clip in enumerate(clips, 1):
            texts = [str(seg.get("text", "")) for seg in clip.get("transcript", [])]
            lengths = sorted(len(text) for text in texts if text)
            if not lengths:
                continue
            median = lengths[len(lengths) // 2]
            cadence = "短句为主" if median < 35 else "短长句交替" if median < 90 else "较长口语段落"
            output.append(
                f"### 真实案例 {i} 的表达统计（仅风格）\n"
                f"节奏：{cadence}。松弛的铺垫逐步推进，少量高光后自然收束。\n"
                "故事事实只来自本次人设、主题与已收到的互动；参考统计不提供人物经历、物件或事件。"
            )
        return "\n\n".join(output)

    def get_random_examples(self, n: int = 3, language: str = "zh") -> str:
        """一键获取格式化的 few-shot examples。"""
        samples = self.sample_diverse(n=n, language=language)
        if not samples:
            return "（无可用的 few-shot examples）"
        return self.format_as_fewshot(samples)
