"""Keep optional opening audio from discarding an otherwise ready script."""
import asyncio


async def wait_for_script(body, opening, opening_wait: float = 2.0, deadline: float | None = None):
    """The script is required; opening may be omitted if it is late or fails.

    Cancelling an executor future cannot stop its worker, but its late result
    is never added to the live generator.
    """
    try:
        await body
        if deadline is not None:
            opening_wait = max(0.01, min(opening_wait, deadline - asyncio.get_running_loop().time() - 1.0))
        try:
            return await asyncio.wait_for(opening, timeout=opening_wait)
        except Exception:
            return []
    finally:
        if not opening.done():
            opening.cancel()
        if not body.done():
            body.cancel()
