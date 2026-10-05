import asyncio
import importlib
import json
from pathlib import Path

import pytest

from echuu import EngineConfig, LiveSession, SessionInput
from tests.test_live_session import Driver


@pytest.mark.asyncio
async def test_web_host_gates_playback_and_labels_estimated_history(
    monkeypatch, tmp_path
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "echuu-web" / "backend")
    )
    adapter = importlib.import_module("services.sdk_storytelling")
    manager = importlib.import_module("state").GlobalStateManager()
    manager.reset_play_gate()
    driver = Driver(1)
    monkeypatch.setattr(
        adapter,
        "LiveSession",
        lambda inputs, config, options: LiveSession(inputs, driver=driver),
    )
    events = []
    ready = asyncio.Event()

    async def broadcast(event):
        events.append(event)
        if event["type"] == "ready":
            ready.set()

    task = asyncio.create_task(
        adapter.run_storytelling(
            state=manager,
            directory=tmp_path,
            broadcast=broadcast,
            inputs=SessionInput(session_id="web", name="n", persona="p", topic="t"),
            config=EngineConfig(),
        )
    )
    await asyncio.wait_for(ready.wait(), 1)
    assert driver.generated == 0
    manager.request_play()
    await asyncio.wait_for(task, 3)
    assert next(e for e in events if e["type"] == "step")[
        "playback_feedback"
    ] == "estimated"
    assert next(e for e in events if e["type"] == "step")["speech"] == "hello"
    history = json.loads((tmp_path / "memory.json").read_text())
    assert len(history["estimated_spoken_history"]) == 1
    assert history["story_points"] == []
    assert manager.current_session is None
