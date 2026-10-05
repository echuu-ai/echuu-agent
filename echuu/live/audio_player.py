"""
音频播放器 - 支持实时流式播放和自然停顿
"""

import os
import random
import subprocess
import time
from pathlib import Path
from typing import Optional

from echuu.diagnostics import diagnostic


class AudioPlayer:
    """
    音频播放器 - 使用 ffplay 播放音频，支持自然停顿
    """

    def __init__(self):
        # 检查 ffplay 是否可用
        self.ffplay_available = self._check_ffplay()

    def _check_ffplay(self) -> bool:
        """检查 ffplay 是否可用"""
        try:
            result = subprocess.run(
                ["ffplay", "-version"],
                capture_output=True,
                timeout=2
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_natural_pause(self, stage: str = "") -> float:
        """
        根据剧本阶段生成自然的停顿时间

        Args:
            stage: 剧本阶段（Hook, Build-up, Climax, Resolution等）

        Returns:
            停顿秒数（1-5秒）
        """
        # 不同阶段有不同的停顿模式
        pause_map = {
            "Hook": (1.5, 3.0),      # 开场：较短停顿，保持吸引力
            "Build-up": (2.0, 4.0),   # 铺垫：中等停顿
            "But": (0.5, 1.5),        # 转折点：很短的停顿制造悬念
            "Contradiction": (1.0, 2.0),  # 矛盾：短停顿
            "Example": (2.5, 4.5),    # 举例：较长停顿
            "Climax": (0.3, 1.0),     # 高潮：极短停顿，保持紧张
            "Resolution": (3.0, 5.0), # 结尾：较长停顿，自然收尾
            "Tangent": (2.0, 3.5),    # 跑题：中等停顿
            "Inner-monologue": (2.5, 4.5),  # 独白：较长停顿，思考感
        }

        min_pause, max_pause = pause_map.get(stage, (1.5, 3.5))
        return random.uniform(min_pause, max_pause)

    def play_audio(self, audio_data: bytes, stage: str = "", auto_close: bool = True) -> float:
        """
        播放音频并返回播放时长

        Args:
            audio_data: 音频二进制数据 (WAV/MP3)
            stage: 剧本阶段（用于计算停顿时间）
            auto_close: 是否自动关闭播放窗口

        Returns:
            播放时长（秒）
        """
        if not audio_data:
            return 0.0

        if not self.ffplay_available:
            diagnostic("  ⚠️ ffplay 不可用，跳过音频播放")
            return 0.0

        # 写入临时文件
        temp_file = Path("temp_audio_play.wav")
        try:
            with open(temp_file, "wb") as f:
                f.write(audio_data)

            # 使用 ffplay 播放
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(temp_file)]

            # 启动播放进程
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            # 等待播放完成
            start_time = time.time()
            process.wait()
            duration = time.time() - start_time

            return duration

        except Exception as e:
            diagnostic(f"  ⚠️ 音频播放失败: {e}")
            return 0.0
        finally:
            # 清理临时文件
            if temp_file.exists():
                temp_file.unlink()

    def play_with_pause(self, audio_data: bytes, stage: str = "") -> float:
        """
        播放音频并在之后添加自然停顿

        Args:
            audio_data: 音频二进制数据
            stage: 剧本阶段

        Returns:
            总时长（播放时长 + 停顿时长）
        """
        play_duration = self.play_audio(audio_data, stage)

        # 播放后添加自然停顿
        pause_duration = self.get_natural_pause(stage)
        diagnostic(f"  ⏸️  自然停顿: {pause_duration:.1f}秒")
        time.sleep(pause_duration)

        return play_duration + pause_duration


class StreamSimulator:
    """
    流式直播模拟器 - 模拟真实直播体验
    """

    def __init__(self):
        self.player = AudioPlayer()

    def simulate_live_stream(
        self,
        generator,
        show_progress: bool = True,
        show_memory: bool = True
    ):
        """
        模拟实时直播流 - 串行播放每段音频，中间有自然停顿

        Args:
            generator: 生成步骤的生成器 (engine.run())
            show_progress: 是否显示进度
            show_memory: 是否显示记忆状态
        """
        diagnostic("\n" + "="*60)
        diagnostic("🎙️  实时流式直播模拟")
        diagnostic("="*60 + "\n")

        total_duration = 0.0

        for result in generator:
            stage = result.get("stage", "")
            speech = result.get("speech", "")
            audio = result.get("audio")
            action = result.get("action", "continue")

            # 显示当前步骤
            step = result.get("step", 0)
            action_icon = {
                "continue": "[CONT]",
                "tease": "[TEASE]",
                "jump": "[JUMP]",
                "improvise": "[IMPROV]",
                "end": "[END]",
            }.get(action, "[CONT]")

            diagnostic(f"\n{'─'*40}")
            diagnostic(f"[{step}] {stage} {action_icon}")
            diagnostic(f"📢 {speech[:80]}{'...' if len(speech) > 80 else ''}")
            diagnostic(f"{'─'*40}")

            # 播放音频（如果可用）
            if audio and self.player.ffplay_available:
                duration = self.player.play_with_pause(audio, stage)
                total_duration += duration
                diagnostic(f"  ✅ 播放完成 (本次: {duration:.1f}s)")
            elif audio:
                diagnostic(f"  💾 音频已生成 ({len(audio)//1024}KB)")
                # 即使不播放，也添加模拟停顿
                pause = self.player.get_natural_pause(stage)
                diagnostic(f"  ⏸️  模拟停顿: {pause:.1f}秒")
                time.sleep(pause)
                total_duration += pause

            # 显示记忆状态（每3步显示一次）
            if show_memory and step % 3 == 0:
                memory = result.get("memory_display", "")
                if memory:
                    diagnostic(f"\n{memory}")

            # 如果是结束动作，停止
            if action == "end":
                break

        diagnostic("\n" + "="*60)
        diagnostic(f"🎬 直播模拟完成！")
        diagnostic(f"   总时长: {total_duration:.1f}秒 ({total_duration/60:.1f}分钟)")
        diagnostic("="*60 + "\n")

        return total_duration
