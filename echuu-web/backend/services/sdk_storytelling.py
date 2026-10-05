import asyncio
import json
from pathlib import Path

from echuu import EngineConfig, Interaction, LiveSession, SessionInput, SessionOptions
from echuu.live.timeline import TimelineWriter


async def run_storytelling(
    *,
    state,
    directory: Path,
    broadcast,
    inputs: SessionInput,
    config: EngineConfig,
    initial_danmaku=(),
):
    """Web 仅负责文件、消息和估算播放反馈，内容编排统一交给安装包。"""
    session = LiveSession(inputs, config, options=SessionOptions())
    state.current_session = session
    timeline = TimelineWriter(
        directory, "storytelling", meta={"name": inputs.name, "topic": inputs.topic}
    )

    async def watch_stop():
        while session.state != "CLOSED":
            if state.stop_requested:
                await session.stop()
                state.request_play()
                return
            await asyncio.sleep(0.1)

    watcher = asyncio.create_task(watch_stop())
    try:
        await session.prepare()
        if session.state == "CLOSED":
            return
        for index, text in enumerate(initial_danmaku):
            session.submit_interaction(
                Interaction(event_id=f"initial-{index}", user_id="initial", text=text)
            )
        step_index = 0
        async for event in session.events():
            if event.type == "ready":
                await broadcast(
                    {
                        "type": "ready",
                        "session_id": inputs.session_id,
                        "mode": "storytelling",
                        "content": "剧本已生成",
                        **event.payload,
                    }
                )
                await state.wait_for_play()
                if not state.stop_requested and session.state == "READY":
                    await session.start()
            elif event.type == "diagnostic":
                await broadcast(event.payload)
            elif event.type == "step" and session.state == "RUNNING":
                step = event.step
                payload = dict(step.payload)
                payload["speech"] = step.speech
                payload["step_id"] = step.step_id
                duration = (
                    step.audio.duration
                    if step.audio
                    else max(0.2, len(step.speech) / 5)
                )
                if step.audio_data:
                    filename = f"step_{step_index}.wav"
                    (directory / filename).write_bytes(step.audio_data)
                    payload["audio_url"] = f"/audio/{inputs.session_id}/{filename}"
                payload.pop("duration", None)
                await session.report_playback(step.step_id, "COMMITTED", estimated=True)
                await broadcast(
                    {
                        "type": "step",
                        **timeline.append(payload, duration=duration),
                        "playback_feedback": "estimated",
                    }
                )
                # Browser legacy protocol has no completion ACK; explicitly label
                # estimates and never export them as confirmed heard facts.
                try:
                    await asyncio.wait_for(session.wait_closed(), duration)
                except asyncio.TimeoutError:
                    if session.state == "RUNNING":
                        await session.report_playback(
                            step.step_id, "PLAYED", estimated=True
                        )
                step_index += 1
            elif event.type == "closed":
                (directory / "memory.json").write_text(
                    json.dumps(
                        {
                            "name": inputs.name,
                            "initial_topic": inputs.topic,
                            "playback_feedback": "estimated",
                            "estimated_spoken_history": [
                                s.model_dump(mode="json")
                                for s in session.estimated_steps
                            ],
                            "story_points": [],
                            "promises": [],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                if event.payload["reason"] not in {"completed", "stopped"}:
                    raise RuntimeError(str(event.payload["reason"]))
                await broadcast(
                    {
                        "type": "success",
                        "session_id": inputs.session_id,
                        "content": "表演已取消"
                        if state.stop_requested
                        else "表演圆满结束",
                    }
                )
    finally:
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await session.aclose()
        state.current_session = None
