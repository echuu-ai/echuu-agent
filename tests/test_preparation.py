import asyncio
import pytest
from echuu.modes.preparation import wait_for_script


def test_ready_script_survives_slow_opening():
    async def scenario():
        loop = asyncio.get_running_loop()
        body, opening = loop.create_future(), loop.create_future()
        body.set_result(None)
        assert await wait_for_script(body, opening, opening_wait=0.01) == []
        assert opening.cancelled()
    asyncio.run(scenario())


def test_ready_opening_is_preserved():
    async def scenario():
        loop = asyncio.get_running_loop()
        body, opening = loop.create_future(), loop.create_future()
        body.set_result(None)
        opening.set_result([{'speech': '你好'}])
        assert await wait_for_script(body, opening) == [{'speech': '你好'}]
    asyncio.run(scenario())


def test_body_failure_is_not_reported_as_ready():
    async def scenario():
        loop = asyncio.get_running_loop()
        body, opening = loop.create_future(), loop.create_future()
        body.set_exception(ValueError('invalid script'))
        with pytest.raises(ValueError, match='invalid script'):
            await wait_for_script(body, opening)
        assert opening.cancelled()
    asyncio.run(scenario())
