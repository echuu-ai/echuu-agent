"""规则化喘气停顿 — 不依赖 TTS API（qwen3-tts 无 SSML/break 支持）。

做法：文本按句末标点切成"呼吸组"，逐组合成，在 PCM 层拼接静音字节。
停顿时长按标点类型定规则（省略号/破折号=思考长停，句号/叹号/问号=换气短停），
可加随机抖动避免机械感。舞台指示（括号段）在合成前剥离，不再被朗读。
"""
from __future__ import annotations

import random
import re
import struct
from typing import Callable, List, Optional

from echuu.diagnostics import diagnostic

SAMPLE_RATE = 24000
_WAV_HEADER_LEN = 44

# 台词里的舞台指示：（轻轻抖耳朵）/(挥手) 这类括号段，限长防误伤真正的插入语长句
_STAGE_DIRECTION_RE = re.compile(r"[（(][^（）()]{1,50}[）)]")

# 呼吸组切分点：句末大标点（保留在组尾）
_BREATH_SPLIT_RE = re.compile(r"([。！？；…]|——)")

_MIN_GROUP_LEN = 4

# 标点 → 基础停顿毫秒；思考类（…——）比换气类（。！？）长。
# 注意引擎会把 speech_rate 推到 1.1x（HYPER_FLUENT），停顿量级要盖得过语速。
_PAUSE_BASE_MS = {
    "…": 650,
    "——": 650,
    "。": 450,
    "！": 400,
    "？": 480,
    "；": 380,
}
_PAUSE_DEFAULT_MS = 320
_PAUSE_JITTER_MS = 90


def strip_stage_directions(text: str) -> str:
    """剥离（…）/(…) 舞台指示，多余空白折叠。"""
    return _STAGE_DIRECTION_RE.sub("", text or "").strip()


def split_breath_groups(text: str, min_len: int = _MIN_GROUP_LEN) -> List[str]:
    """按句末标点切呼吸组；过短的组并入前一组，保证拼回原文无损。"""
    if not text:
        return []
    parts = _BREATH_SPLIT_RE.split(text)
    raw: List[str] = []
    for piece in parts:
        if not piece:
            continue
        if raw and _BREATH_SPLIT_RE.fullmatch(piece):
            raw[-1] += piece  # 标点挂回前一段尾部
        else:
            raw.append(piece)
    groups: List[str] = []
    for seg in raw:
        if groups and (len(seg) < min_len or len(groups[-1]) < min_len):
            groups[-1] += seg
        else:
            groups.append(seg)
    return groups


def chunk_text(text: str, max_chars: int = 42) -> List[str]:
    """把长台词按呼吸组贪心聚合成 ≤max_chars 的短句块（拼回原文无损）。

    用于把一行 80+ 字的台词切成多个 step：字幕短、动作切换勤、
    前端 step 间自带换气间隙。短文本原样单块返回。
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    for group in split_breath_groups(text):
        if chunks and len(chunks[-1]) + len(group) <= max_chars:
            chunks[-1] += group
        else:
            chunks.append(group)
    return chunks


def pause_ms_after(segment: str, jitter: int = _PAUSE_JITTER_MS) -> int:
    """规则停顿：按组尾标点查表，叠加 ±jitter 随机抖动。"""
    base = _PAUSE_DEFAULT_MS
    if segment:
        if segment.endswith("——"):
            base = _PAUSE_BASE_MS["——"]
        else:
            base = _PAUSE_BASE_MS.get(segment[-1], _PAUSE_DEFAULT_MS)
    if jitter > 0:
        base += random.randint(-jitter, jitter)
    return max(base, 0)


def wrap_pcm_as_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """16bit mono PCM → WAV（与 workflow/backend pcm_to_wav 同构，44 字节头）。"""
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(pcm), b"WAVE",
        b"fmt ", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b"data", len(pcm),
    )
    return header + pcm


def _extract_pcm(wav: bytes) -> bytes:
    if wav[:4] == b"RIFF" and len(wav) > _WAV_HEADER_LEN:
        return wav[_WAV_HEADER_LEN:]
    return wav


def stitch_wavs_with_pauses(
    wavs: List[bytes], pauses_ms: List[int], sample_rate: int = SAMPLE_RATE,
) -> bytes:
    """段间插静音拼接：pauses_ms[i] 是第 i 段与第 i+1 段之间的停顿。"""
    chunks: List[bytes] = []
    for i, wav in enumerate(wavs):
        chunks.append(_extract_pcm(wav))
        if i < len(wavs) - 1:
            ms = pauses_ms[i] if i < len(pauses_ms) else _PAUSE_DEFAULT_MS
            chunks.append(b"\x00\x00" * int(sample_rate * ms / 1000))
    return wrap_pcm_as_wav(b"".join(chunks), sample_rate)


def synthesize_with_breath(
    synth: Callable[[str], Optional[bytes]],
    text: str,
    *,
    sample_rate: int = SAMPLE_RATE,
    rng_jitter: int = _PAUSE_JITTER_MS,
) -> Optional[bytes]:
    """剥离舞台指示 → 切呼吸组 → 逐组合成 → 静音拼接。

    单组文本直接透传合成；某组失败跳过（降级），全部失败返回 None。
    """
    clean = strip_stage_directions(text)
    if not clean:
        return None
    groups = split_breath_groups(clean)
    if len(groups) <= 1:
        return synth(clean)

    synthesized: List[tuple] = []  # (group, wav)
    for group in groups:
        try:
            wav = synth(group)
        except Exception as exc:  # noqa: BLE001 — 单组失败不拖垮整句
            diagnostic(f"[breath] 分段合成失败（跳过）: {exc}")
            wav = None
        if wav:
            synthesized.append((group, wav))
    if not synthesized:
        return None
    if len(synthesized) == 1:
        return synthesized[0][1]

    pauses = [pause_ms_after(g, jitter=rng_jitter) for g, _ in synthesized[:-1]]
    return stitch_wavs_with_pauses([w for _, w in synthesized], pauses, sample_rate)
