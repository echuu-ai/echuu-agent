"""学唱模式预生产管线（全 API，零自部署）。

现成歌曲文件（用户提供）
  → Replicate 托管 Demucs 分离人声/伴奏（无 token 时降级：整曲当人声，无伴奏轨）
  → pydub 静音检测切乐句，挑 3-4 个"有戏"的句子
  → RVC 转角色声线（RVC_MODEL_URL 未配置时降级：保留原唱声线）
  → degrade 做旧出 bad/mid 版本
  → LLM 生成三段式碎碎念（persona 化）
  → step 事件列表：talk（TTS 字节）/ sing（wav 落盘 + audio_url 预置）

三段式结构（4-5 分钟）：
  Act1 试唱崩坏（bad）→ Act2 逐句练习（1 句一次过、1 句反复失败）→ Act3 成果展示（good + 微瑕疵）
"""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path
from typing import Callable, List, Optional

from echuu.diagnostics import diagnostic

from .degrade import degrade_vocal

TALK_GAP_INSTRUCTION = {
    "intro": "随意放松的开场语气，跃跃欲试",
    "fail": "崩溃又好笑，带着不甘心的吐槽",
    "before": "认真准备，自我打气",
    "after_fail": "懊恼、碎碎念，但没有放弃",
    "after_good": "惊喜，自己都没想到，藏不住的得意",
    "final_intro": "深呼吸，认真而略带紧张",
    "final_review": "如释重负，害羞又嘴硬的自评",
}

_MURMUR_PROMPT = """你是虚拟主播「{name}」。人设：{persona}
背景：{background}

你在直播学唱一首歌{song_label}。整个过程分三段：
- Act1: 听了一遍就试唱，结果崩了
- Act2: 逐句练习 {n_phrases} 个句子。其中第 {lucky_idx} 句你一次就唱对了（连自己都惊讶）；第 {hard_idx} 句你失败了两次才勉强过
- Act3: 最后完整唱一段，唱完自评

为每个节点写台词（口语，15-40字，符合你的人设口癖）。输出 JSON（不要 markdown 代码块）：
{{
  "intro": "<开场台词：宣布今天要学这首歌，跃跃欲试>",
  "first_fail": "<试唱崩掉之后的即时反应>",
  "phrases": [
    {{"before": "<练这一句之前说的话>", "after_fail": "<唱砸后的碎碎念>", "after_good": "<唱对之后的反应>"}},
    ...共 {n_phrases} 个
  ],
  "final_intro": "<最后完整唱之前深呼吸时说的话>",
  "final_review": "<唱完的自评，不完美但真诚>"
}}

注意：第 {lucky_idx} 句的 after_good 要体现出乎自己意料的惊喜；第 {hard_idx} 句的 after_fail 要有两句话用「||」分隔（对应两次失败）。
只输出 JSON。"""


def _parse_json_obj(text: str) -> dict:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*})\s*```", text, re.S)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM 输出中找不到 JSON 对象: {text[:200]}")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- separation

def separate_song(song_path: str, work_dir: Path, phase: Callable) -> tuple[bytes, Optional[bytes]]:
    """Demucs 分离 → (vocals_wav, accompaniment_wav|None)。

    无 REPLICATE_API_TOKEN 时降级：整曲作为人声、无伴奏轨（管线仍可跑通）。
    """
    from pydub import AudioSegment

    token = os.getenv("REPLICATE_API_TOKEN")
    if not token:
        phase("⚠️ 未配置 REPLICATE_API_TOKEN，跳过人声分离（整曲当人声，无伴奏轨）")
        seg = AudioSegment.from_file(song_path).set_channels(1)
        buf = io.BytesIO()
        seg.export(buf, format="wav")
        return buf.getvalue(), None

    import replicate
    import requests as _requests

    phase("Replicate Demucs 分离人声/伴奏中（约 1-2 分钟）...")
    model = os.getenv("DEMUCS_MODEL", "ryan5453/demucs:5a7041cc9b82e5a558fea6b3d7b12dea89625e89da33f0447bd727c2d0ab9e77")
    with open(song_path, "rb") as f:
        output = replicate.run(model, input={
            "audio": f,
            "stem": "vocals",          # two-stem 模式：vocals + no_vocals
            "output_format": "wav",
        })

    def _fetch(item) -> bytes:
        url = item.get("url") if isinstance(item, dict) else str(item)
        return _requests.get(url, timeout=120).content

    out = output if isinstance(output, dict) else {}
    vocals = _fetch(out.get("vocals")) if out.get("vocals") else None
    accomp = _fetch(out.get("no_vocals") or out.get("other")) if (out.get("no_vocals") or out.get("other")) else None
    if not vocals:
        raise RuntimeError(f"Demucs 输出缺少 vocals: keys={list(out.keys())}")
    phase("分离完成")
    return vocals, accomp


# ---------------------------------------------------------------- phrases

def extract_phrases(vocals_wav: bytes, max_phrases: int = 4) -> list[dict]:
    """静音检测切乐句，挑中段最长的几句。返回 [{start_ms, end_ms}]。"""
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent

    seg = AudioSegment.from_file(io.BytesIO(vocals_wav), format="wav")
    spans = detect_nonsilent(
        seg,
        min_silence_len=400,
        silence_thresh=seg.dBFS - 14,
        seek_step=50,
    )
    # 合并间隔 <600ms 的相邻段，限制单句 2.5-12s
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s - merged[-1][1] < 600:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    phrases = [
        {"start_ms": s, "end_ms": e}
        for s, e in merged
        if 2500 <= e - s <= 12000
    ]
    if not phrases:  # 全曲无合格乐句时兜底：固定切 6s 段
        phrases = [
            {"start_ms": t, "end_ms": min(t + 6000, len(seg))}
            for t in range(0, len(seg), 8000)
        ][:max_phrases]
    # 挑最长的 max_phrases 句（偏好中段），按时间排序
    phrases.sort(key=lambda p: p["end_ms"] - p["start_ms"], reverse=True)
    picked = sorted(phrases[:max_phrases], key=lambda p: p["start_ms"])
    return picked


# ---------------------------------------------------------------- rvc

def convert_to_character_voice(vocal_wav: bytes, phase: Callable) -> bytes:
    """RVC 转角色声线。未配置 RVC_MODEL_URL 时原样返回（保留原唱声线）。"""
    rvc_url = os.getenv("RVC_MODEL_URL")
    token = os.getenv("REPLICATE_API_TOKEN")
    if not (rvc_url and token):
        return vocal_wav

    import replicate
    import requests as _requests

    model = os.getenv("RVC_MODEL", "pseudoram/rvc-v2:d18e0a3714e19a4f2b6cbe0dee5e5c47c47e4767b4f4d9b6a07ecbf0d2ba32e5")
    output = replicate.run(model, input={
        "input_audio": io.BytesIO(vocal_wav),
        "rvc_model": "CUSTOM",
        "custom_rvc_model_download_url": rvc_url,
        "pitch_change": os.getenv("RVC_PITCH_CHANGE", "no-change"),
        "output_format": "wav",
    })
    url = output.get("url") if isinstance(output, dict) else str(output)
    return _requests.get(url, timeout=120).content


# ---------------------------------------------------------------- assembly

def _mix_with_bgm(vocal_wav: bytes, accomp_wav: Optional[bytes],
                  start_ms: int, end_ms: int) -> bytes:
    """人声句叠对应时段的伴奏（伴奏 -8dB）。无伴奏轨则原样返回。"""
    from pydub import AudioSegment

    vocal = AudioSegment.from_file(io.BytesIO(vocal_wav), format="wav")
    if not accomp_wav:
        return vocal_wav
    accomp = AudioSegment.from_file(io.BytesIO(accomp_wav), format="wav")
    bed = accomp[start_ms:start_ms + len(vocal)] - 8
    mixed = bed.overlay(vocal)
    buf = io.BytesIO()
    mixed.export(buf, format="wav")
    return buf.getvalue()


def produce_singing_events(
    engine,
    name: str,
    persona: str,
    background: str,
    topic: str,
    source: dict,
    session_dir: Path,
    on_phase: Optional[Callable[[str], None]] = None,
) -> List[dict]:
    """预生产：完整产出学唱 step 事件列表。"""
    from pydub import AudioSegment

    song_path = (source or {}).get("song_path")
    if not song_path or not Path(song_path).exists():
        raise ValueError("singing_learn 模式需要 source.song_path（本地歌曲文件）")
    song_title = (source or {}).get("song_title", "")

    def phase(msg: str):
        diagnostic(f"[singing] {msg}")
        if on_phase:
            on_phase(msg)

    session_dir = Path(session_dir)
    sing_idx = 0

    def save_sing(wav_bytes: bytes, label: str) -> dict:
        """sing 音频落盘，返回预置 audio_url + duration 的事件片段。"""
        nonlocal sing_idx
        fname = f"sing_{sing_idx}_{label}.wav"
        sing_idx += 1
        (session_dir / fname).write_bytes(wav_bytes)
        seg = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
        return {
            "audio_url": f"/audio/{session_dir.name}/{fname}",
            "duration": round(len(seg) / 1000, 3),
        }

    # ---- Phase A: 分离 ----
    phase("Phase A: 人声/伴奏分离...")
    vocals, accomp = separate_song(song_path, session_dir, phase)

    # ---- Phase B: 切乐句 ----
    phase("Phase B: 乐句切分...")
    phrases = extract_phrases(vocals)
    n = len(phrases)
    phase(f"选出 {n} 个乐句: " + ", ".join(
        f"{p['start_ms']/1000:.0f}-{p['end_ms']/1000:.0f}s" for p in phrases))
    if n < 2:
        raise RuntimeError("乐句太少，无法编排学唱节目（换首人声更清晰的歌试试）")

    # StructureBreaker 式命运分配：1 句一次过（lucky），1 句双重失败（hard）
    lucky_idx = 1 if n >= 2 else 0
    hard_idx = n - 1

    # ---- Phase C: RVC + 做旧 ----
    phase("Phase C: 声线转换 + 多质量版本生成...")
    vocal_full = AudioSegment.from_file(io.BytesIO(vocals), format="wav")
    versions: list[dict] = []  # per phrase: {good, mid, bad} wav bytes
    for i, p in enumerate(phrases):
        cut = vocal_full[p["start_ms"]:p["end_ms"]]
        buf = io.BytesIO()
        cut.export(buf, format="wav")
        raw = buf.getvalue()
        good = convert_to_character_voice(raw, phase)
        versions.append({
            "good": degrade_vocal(good, "good", seed=i),
            "mid": degrade_vocal(good, "mid", seed=i + 100),
            "bad": degrade_vocal(good, "bad", seed=i + 200),
        })
    if not os.getenv("RVC_MODEL_URL"):
        phase("⚠️ 未配置 RVC_MODEL_URL，演唱保留原唱声线（角色音色训练完成后自动切换）")

    # ---- Phase D: 碎碎念脚本 ----
    phase("Phase D: 生成碎碎念脚本...")
    murmur_raw = engine.llm.call(_MURMUR_PROMPT.format(
        name=name, persona=persona, background=background or "（无）",
        song_label=f"《{song_title}》" if song_title else "",
        n_phrases=n, lucky_idx=lucky_idx + 1, hard_idx=hard_idx + 1,
    ), max_tokens=2000)
    murmur = _parse_json_obj(murmur_raw)

    # ---- Phase E: 编排三段式 timeline ----
    phase("Phase E: 编排 + TTS...")
    events: List[dict] = []

    def talk(text: str, kind: str):
        if not text:
            return
        engine.tts.set_instruction(TALK_GAP_INSTRUCTION.get(kind, ""))
        audio = None
        for _ in range(2):
            try:
                audio = engine.tts.synthesize(text)
                if audio:
                    break
            except Exception as exc:
                diagnostic(f"[singing] TTS 失败: {exc}")
        events.append({
            "type": "step", "mode": "singing_learn", "event": "talk",
            "speech": text, "stage": kind, "audio": audio,
        })

    def sing(idx: int, quality: str):
        p = phrases[idx]
        wav = _mix_with_bgm(versions[idx][quality], accomp, p["start_ms"], p["end_ms"])
        meta = save_sing(wav, f"p{idx}_{quality}")
        events.append({
            "type": "step", "mode": "singing_learn", "event": "sing",
            "speech": "", "stage": f"sing_{quality}",
            "phrase_index": idx, "quality": quality, **meta,
        })

    # Act 1: 开场 + 试唱崩坏（前两句 bad 连唱）
    talk(murmur.get("intro", ""), "intro")
    sing(0, "bad")
    talk(murmur.get("first_fail", ""), "fail")

    # Act 2: 逐句练习
    phrase_murmurs = murmur.get("phrases", [])
    for i in range(n):
        pm = phrase_murmurs[i] if i < len(phrase_murmurs) else {}
        talk(pm.get("before", ""), "before")
        if i == lucky_idx:
            sing(i, "good")                     # 一次过，惊喜
            talk(pm.get("after_good", ""), "after_good")
        elif i == hard_idx:
            fails = str(pm.get("after_fail", "")).split("||")
            sing(i, "bad")
            talk(fails[0].strip(), "after_fail")
            sing(i, "bad")                      # 第二次还是砸
            talk((fails[1] if len(fails) > 1 else fails[0]).strip(), "after_fail")
            sing(i, "mid")                      # 勉强过
            talk(pm.get("after_good", ""), "after_good")
        else:
            sing(i, "bad")
            talk(pm.get("after_fail", ""), "after_fail")
            sing(i, "mid")
            talk(pm.get("after_good", ""), "after_good")

    # Act 3: 成果展示（全部乐句 good 连唱，最后一句留微瑕疵 = mid）
    talk(murmur.get("final_intro", ""), "final_intro")
    for i in range(n):
        sing(i, "good" if i < n - 1 else "mid")
    talk(murmur.get("final_review", ""), "final_review")

    phase(f"预生产完成: {len(events)} 个事件（{sing_idx} 段演唱）")
    return events
