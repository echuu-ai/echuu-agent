import asyncio

import pytest

from echuu import LiveSession
from echuu.session_extensions import GiftBatchInput, GiftItem, SessionArtifacts
from echuu.session_types import CapacityExceeded, InvalidTransition, PerformanceStep
from tests.test_live_session import Driver, inputs


class ExtendedDriver(Driver):
    def prepare_gift(self, batch, allow_effect):
        self.effect = allow_effect
        return PerformanceStep(
            step_id=batch.batch_id,
            line_id="thanks",
            speech="谢谢",
            payload={"purpose": "gift_ack", "batch_id": batch.batch_id},
        )

    def finish_gift(self, batch_id, played):
        self.result = (batch_id, played)

    def export_artifacts(self):
        return SessionArtifacts(script={"lines": ["hello"]}, dossier={"name": "host"})


def batch(key="gift"):
    return GiftBatchInput(
        batch_id=key,
        start_within=10,
        items=[
            GiftItem(
                event_id="event",
                user_id="user",
                user_name="观众",
                gift_id="baozi",
                name="包子",
                value=12,
                effect="温暖",
                sentences=2,
            )
        ],
    )


@pytest.mark.asyncio
async def test_preview_gift_is_confirmed_only_after_playback():
    driver = ExtendedDriver()
    session = LiveSession(inputs(), driver=driver)
    await session.prepare()
    assert session.export_artifacts().script == {"lines": ["hello"]}
    await session.start(autoplay=False)
    done = session.submit_gift_batch(batch())
    assert session.submit_gift_batch(batch()) is done
    with pytest.raises(InvalidTransition):
        session.submit_gift_batch(
            batch().model_copy(
                update={"items": [batch().items[0].model_copy(update={"value": 3})]}
            )
        )
    with pytest.raises(CapacityExceeded):
        session.submit_gift_batch(batch("other"))
    async for event in session.events():
        if event.type == "step":
            assert driver.generated == 0
            assert not driver.effect
            assert not done.done()
            await session.report_playback(event.step.step_id, "COMMITTED")
            await session.report_playback(event.step.step_id, "PLAYED")
            assert await asyncio.wait_for(done, 1)
            await session.stop()
    assert driver.result == ("gift", True)
    assert session.export_artifacts().memory["playback_source"] == "confirmed"


@pytest.mark.asyncio
async def test_graceful_end_drains_only_committed_step():
    driver = ExtendedDriver()
    session = LiveSession(inputs(), driver=driver)
    await session.prepare()
    await session.start()
    async for event in session.events():
        if event.type == "step":
            await session.report_playback(event.step.step_id, "COMMITTED")
            closing = asyncio.create_task(session.finish())
            await asyncio.sleep(0)
            assert not closing.done()
            await session.report_playback(event.step.step_id, "PLAYED")
            await asyncio.wait_for(closing, 1)
    assert driver.generated == 1
    assert len(session.played_steps) == 1


@pytest.mark.asyncio
async def test_hard_stop_resolves_gift_without_success():
    session = LiveSession(inputs(), driver=ExtendedDriver())
    await session.prepare()
    done = session.submit_gift_batch(batch())
    await session.stop()
    assert await done is False
