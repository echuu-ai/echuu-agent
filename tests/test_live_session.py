import asyncio
import threading

import pytest

from echuu import (
    Interaction,
    LiveSession,
    PerformanceStep,
    SessionInput,
    SessionOptions,
)
from echuu.session_types import CapacityExceeded, InvalidTransition


def inputs():
    return SessionInput(session_id="test", name="host", persona="kind", topic="a walk")


class Driver:
    def __init__(self, steps=2):
        self.remaining = steps
        self.generated = 0
        self.heard = []
        self.interactions = []
        self.closed = threading.Event()

    def prepare(self):
        return ["hello"]

    def next_step(self):
        if not self.remaining:
            return None
        self.remaining -= 1
        self.generated += 1
        return PerformanceStep(
            step_id=str(self.generated),
            line_id=str(self.generated),
            speech="hello",
            payload={},
        )

    def tick(self):
        return []

    def submit(self, interaction):
        self.interactions.append(interaction)

    def played(self, step):
        self.heard.append(step.step_id)

    def close(self):
        self.closed.set()


@pytest.mark.asyncio
async def test_lifecycle_acknowledgement_and_bounded_prefetch():
    driver = Driver()
    session = LiveSession(inputs(), driver=driver)
    with pytest.raises(InvalidTransition):
        await session.start()
    await session.prepare()
    assert driver.generated == 0
    await session.start()
    events = []
    async for event in session.events():
        events.append(event)
        if event.type == "step":
            step = event.step
            assert step.step_id not in driver.heard
            await asyncio.sleep(0.02)
            assert driver.generated == int(step.step_id)
            with pytest.raises(InvalidTransition):
                await session.report_playback(step.step_id, "PLAYED")
            await session.report_playback(step.step_id, "COMMITTED")
            await session.report_playback(step.step_id, "PLAYED")
            assert not await session.report_playback(step.step_id, "PLAYED")
    assert driver.heard == ["1", "2"]
    assert len(session.played_steps) == 2
    assert session.reason == "completed"
    assert [e.sequence for e in events] == sorted({e.sequence for e in events})
    await session.aclose()
    assert await asyncio.to_thread(driver.closed.wait, 1)


@pytest.mark.asyncio
async def test_stop_during_playback_and_late_ack():
    driver = Driver()
    session = LiveSession(inputs(), driver=driver)
    await session.prepare()
    await session.start()
    async for event in session.events():
        if event.type == "step":
            await session.report_playback(event.step.step_id, "COMMITTED")
            await session.stop()
            with pytest.raises(InvalidTransition):
                await session.report_playback(event.step.step_id, "PLAYED")
    assert driver.heard == []
    assert not session.played_steps
    await session.stop()


@pytest.mark.asyncio
async def test_cancelled_step_never_enters_heard_history():
    driver = Driver(1)
    session = LiveSession(inputs(), driver=driver)
    await session.prepare()
    await session.start()
    async for event in session.events():
        if event.type == "step":
            await session.report_playback(event.step.step_id, "CANCELLED")
    assert not driver.heard


@pytest.mark.asyncio
async def test_interaction_dedup_capacity_and_session_scope():
    session = LiveSession(
        inputs(), driver=Driver(), options=SessionOptions(interaction_capacity=1)
    )
    await session.prepare()
    item = Interaction(event_id="a", user_id="viewer", text="hello")
    assert session.submit_interaction(item)
    assert not session.submit_interaction(item)
    with pytest.raises(CapacityExceeded):
        session.submit_interaction(item.model_copy(update={"event_id": "b"}))
    await session.stop()
    with pytest.raises(InvalidTransition):
        session.submit_interaction(item)
    other = LiveSession(inputs(), driver=Driver())
    await other.prepare()
    assert other.submit_interaction(item)
    await other.stop()


@pytest.mark.asyncio
async def test_stop_during_slow_prepare_discards_late_result():
    gate = threading.Event()
    entered = threading.Event()
    driver = Driver()

    def slow():
        entered.set()
        gate.wait(1)
        return ["late"]

    driver.prepare = slow
    session = LiveSession(inputs(), driver=driver)
    task = asyncio.create_task(session.prepare())
    assert await asyncio.to_thread(entered.wait, 1)
    await session.stop()
    gate.set()
    await task
    assert [e.type async for e in session.events()] == ["closed"]
    assert await asyncio.to_thread(driver.closed.wait, 1)


@pytest.mark.asyncio
async def test_playback_timeout_terminates_without_fact_commit():
    session = LiveSession(
        inputs(), driver=Driver(), options=SessionOptions(playback_timeout=0.02)
    )
    await session.prepare()
    await session.start()
    events = [e async for e in session.events()]
    assert events[-1].payload["reason"] == "SessionTimeout"
    assert not session.played_steps


@pytest.mark.asyncio
async def test_event_queue_backpressure_is_explicit():
    session = LiveSession(
        inputs(),
        driver=Driver(),
        options=SessionOptions(event_capacity=2, operation_timeout=0.02),
    )
    await session.prepare()
    await session._emit("diagnostic")
    with pytest.raises(CapacityExceeded):
        await session._emit("diagnostic")
    await session.stop()


def test_audio_bytes_do_not_leak_into_serialized_contract():
    step = PerformanceStep(
        step_id="s", line_id="l", speech="hello", payload={}, audio_data=b"secret-audio"
    )
    assert "audio_data" not in step.model_dump(mode="json")


def test_engine_history_defers_to_playback_acknowledgement():
    from tests.test_danmaku_interleave import _engine_with, _make_show

    engine = _engine_with(_make_show(), [])
    engine.state.spoken_history = []
    engine.defer_playback_history = True
    first = next(engine.run(max_steps=1))
    assert first["speech"]
    assert not engine.state.spoken_history


@pytest.mark.asyncio
async def test_real_storytelling_driver_through_public_session():
    from echuu.config import EngineConfig
    from echuu.storytelling import StorytellingDriver
    from tests.test_danmaku_interleave import _engine_with, _make_show

    engine = _engine_with(_make_show(), [])
    engine.state.spoken_history = []
    from echuu.live.qwen_tts import pcm_to_wav

    engine.tts.synthesize = lambda text: pcm_to_wav(b"\0\0" * 240)
    engine.setup = lambda **kwargs: engine.state
    options = SessionOptions(rundown=False, grounding=False)
    driver = StorytellingDriver(
        inputs().model_copy(update={"max_steps": 1}),
        EngineConfig(),
        options,
        engine=engine,
    )
    session = LiveSession(inputs(), driver=driver, options=options)
    await session.prepare()
    await session.start()
    steps = []
    async for event in session.events():
        if event.type == "step":
            assert not engine.state.spoken_history
            steps.append(event.step)
            await session.report_playback(event.step.step_id, "COMMITTED")
            await session.report_playback(event.step.step_id, "PLAYED")
    assert steps
    assert engine.state.spoken_history[0]["text"] == steps[0].speech
    assert session.reason == "completed"


@pytest.mark.asyncio
async def test_estimates_are_separate_from_confirmed_facts():
    session = LiveSession(inputs(), driver=Driver(1))
    await session.prepare()
    await session.start()
    async for event in session.events():
        if event.type == "step":
            await session.report_playback(
                event.step.step_id, "COMMITTED", estimated=True
            )
            await session.report_playback(event.step.step_id, "PLAYED", estimated=True)
    assert not session.played_steps
    assert len(session.estimated_steps) == 1


@pytest.mark.asyncio
async def test_stop_releases_prepare_without_waiting_for_worker():
    gate, entered = threading.Event(), threading.Event()
    driver = Driver()

    def prepare():
        entered.set()
        gate.wait(2)
        return []

    driver.prepare = prepare
    session = LiveSession(inputs(), driver=driver)
    task = asyncio.create_task(session.prepare())
    assert await asyncio.to_thread(entered.wait, 1)
    try:
        await session.stop()
        await asyncio.wait_for(task, 0.2)
        assert not driver.closed.is_set()  # Worker really is still alive.
    finally:
        gate.set()
    assert await asyncio.to_thread(driver.closed.wait, 1)


@pytest.mark.asyncio
async def test_recorder_receives_terminal_event_once():
    from unittest.mock import AsyncMock

    recorder = type("Recorder", (), {"record": AsyncMock()})()
    session = LiveSession(inputs(), driver=Driver(), recorder=recorder)
    await session.prepare()
    await session.stop()
    await session.stop()
    assert [call.args[0].type for call in recorder.record.call_args_list] == [
        "ready",
        "closed",
    ]
