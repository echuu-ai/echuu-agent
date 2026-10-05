import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from time import monotonic
from typing import Literal, TypeVar

from pydantic import JsonValue
from typing_extensions import Self

from .config import EngineConfig
from .session_extensions import (
    ArtifactDriver,
    GiftBatchInput,
    GiftDriver,
    SessionArtifacts,
)
from .session_types import (
    CapacityExceeded,
    Interaction,
    InvalidTransition,
    PerformanceStep,
    SessionDriver,
    SessionEvent,
    SessionInput,
    SessionOptions,
    SessionRecorder,
    SessionTimeout,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)


class LiveSession:
    """单消费者、单个待播放步骤的异步会话；只有宿主回执能提交已播事实。"""

    def __init__(
        self,
        inputs: SessionInput,
        config: EngineConfig | None = None,
        *,
        options: SessionOptions | None = None,
        driver: SessionDriver | None = None,
        recorder: SessionRecorder | None = None,
    ):
        self.inputs = inputs
        self.options = options or SessionOptions()
        if driver is None:
            from .storytelling import StorytellingDriver

            driver = StorytellingDriver(inputs, config or EngineConfig(), self.options)
        from . import __version__

        self.manifest = {
            "sdk_version": __version__,
            "provider": config.provider if config else "injected",
            "model": config.model if config else None,
            "story_pipeline": config.story_pipeline if config else "injected",
        }
        self._driver = driver
        self._recorder = recorder
        self.state = "CREATED"
        self.reason: str | None = None
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="echuu-session"
        )
        self._queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            self.options.event_capacity
        )
        self._interactions: asyncio.Queue[Interaction] = asyncio.Queue(
            self.options.interaction_capacity
        )
        self._seen: set[tuple[str, str, str]] = set()
        self._sequence = 0
        self._task: asyncio.Task[None] | None = None
        self._terminal: SessionEvent | None = None
        self._closed = asyncio.Event()
        self._ack = asyncio.Event()
        self._pending: PerformanceStep | None = None
        self._playback: dict[str, str] = {}
        self._played: list[PerformanceStep] = []
        self._estimated: list[PerformanceStep] = []
        self._consumer = False
        self._accepting = True
        self._started_at = monotonic()
        self.metrics: dict[str, float] = {}
        self._artifacts = SessionArtifacts()
        self._autoplay = True
        self._draining = False
        self._gifts: dict[str, tuple[GiftBatchInput, asyncio.Future[bool]]] = {}
        self._gift_pending: tuple[GiftBatchInput, float] | None = None
        self._gift_current: str | None = None

    @property
    def played_steps(self) -> tuple[PerformanceStep, ...]:
        return tuple(self._played)

    @property
    def estimated_steps(self) -> tuple[PerformanceStep, ...]:
        return tuple(self._estimated)

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def _call(
        self, function: Callable[..., T], *args: object, timeout: float | None = None
    ) -> T:
        future = asyncio.get_running_loop().run_in_executor(
            self._pool, partial(function, *args)
        )
        try:
            return await asyncio.wait_for(
                future, timeout or self.options.operation_timeout
            )
        except asyncio.TimeoutError as exc:
            raise SessionTimeout("Engine operation exceeded its deadline") from exc

    def _event(
        self,
        kind: Literal["ready", "step", "playback", "diagnostic", "closed"],
        *,
        payload: dict[str, JsonValue] | None = None,
        step: PerformanceStep | None = None,
    ) -> SessionEvent:
        self._sequence += 1
        return SessionEvent(
            event_id=f"{self.inputs.session_id}:{self._sequence}",
            session_id=self.inputs.session_id,
            sequence=self._sequence,
            type=kind,
            payload=payload or {},
            step=step,
        )

    async def _emit(
        self,
        kind: Literal["ready", "step", "playback", "diagnostic", "closed"],
        *,
        payload: dict[str, JsonValue] | None = None,
        step: PerformanceStep | None = None,
    ) -> None:
        if self.state in {"STOPPING", "CLOSED"}:
            return
        event = self._event(kind, payload=payload, step=step)
        try:
            await asyncio.wait_for(
                self._queue.put(event), self.options.operation_timeout
            )
            if self._recorder:
                await asyncio.wait_for(
                    self._recorder.record(event), self.options.operation_timeout
                )
        except asyncio.TimeoutError as exc:
            raise CapacityExceeded(
                "Event consumer or recorder is not keeping up"
            ) from exc

    async def prepare(self) -> None:
        if self.state != "CREATED":
            raise InvalidTransition("prepare requires CREATED")
        self.state = "PREPARING"
        start = monotonic()
        try:
            preparing = asyncio.create_task(
                self._call(
                    self._driver.prepare, timeout=self.options.preparation_timeout
                )
            )
            closed = asyncio.create_task(self.wait_closed())
            try:
                done, _ = await asyncio.wait(
                    {preparing, closed}, return_when=asyncio.FIRST_COMPLETED
                )
                if preparing not in done:
                    preparing.cancel()
                    return
                preview = preparing.result()
            finally:
                for task in (preparing, closed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(preparing, closed, return_exceptions=True)
            if self.state != "PREPARING":
                return
            self.metrics["preparation_seconds"] = monotonic() - start
            await self._refresh_artifacts()
            self.state = "READY"
            await self._emit(
                "ready",
                payload={
                    "preview": preview,
                    "manifest": dict(self.manifest),
                    "metrics": {key: value for key, value in self.metrics.items()},
                },
            )
        except BaseException:
            await self.stop("preparation_failed")
            raise

    def export_artifacts(self) -> SessionArtifacts:
        """返回缓存的只读资料，不访问工作线程中的可变引擎状态。"""
        memory = dict(self._artifacts.memory)
        candidates = memory.pop("candidate_promises", [])
        spoken = "\n".join(step.speech for step in self._played)
        promises: list[JsonValue] = []
        if isinstance(candidates, list):
            promises = [
                p
                for p in candidates
                if isinstance(p, dict)
                and isinstance(p.get("content"), str)
                and p["content"]
                and str(p["content"]) in spoken
                and not p.get("fulfilled")
            ]
        memory.update(
            {
                "name": self.inputs.name,
                "initial_topic": self.inputs.topic,
                "playback_source": "confirmed"
                if self._played
                else "estimated"
                if self._estimated
                else "unplayed",
                "spoken_history": [
                    step.model_dump(mode="json") for step in self._played
                ],
                "estimated_spoken_history": [
                    step.model_dump(mode="json") for step in self._estimated
                ],
                "story_points": [step.speech for step in self._played[-3:]],
                "promises": promises,
            }
        )
        return self._artifacts.model_copy(update={"memory": memory}, deep=True)

    async def _refresh_artifacts(self) -> None:
        if isinstance(self._driver, ArtifactDriver):
            self._artifacts = await self._call(self._driver.export_artifacts)

    def set_autoplay(self, enabled: bool) -> None:
        self._autoplay = enabled

    def submit_gift_batch(self, batch: GiftBatchInput) -> asyncio.Future[bool]:
        if self.state not in {"READY", "RUNNING"} or not self._accepting:
            raise InvalidTransition("Session is not accepting gifts")
        previous = self._gifts.get(batch.batch_id)
        if previous:
            if previous[0] != batch:
                raise InvalidTransition("Gift batch contents changed")
            return previous[1]
        if not isinstance(self._driver, GiftDriver):
            raise InvalidTransition("Driver does not support reliable gifts")
        if (
            self._gift_pending
            or self._gift_current
            or len(self._gifts) >= self.options.max_interactions
        ):
            raise CapacityExceeded("Gift capacity reached")
        done: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._gifts[batch.batch_id] = (batch, done)
        self._gift_pending = (batch, monotonic() + batch.start_within)
        return done

    async def finish(self) -> None:
        """封闭新输入，允许已提交的一段有界播完；未提交内容立即取消。"""
        self._accepting = False
        self._draining = True
        if (
            self.state != "RUNNING"
            or not self._pending
            or self._playback.get(self._pending.step_id) not in {"COMMITTED", "PLAYED"}
        ):
            await self.stop()
            return
        try:
            await asyncio.wait_for(
                self.wait_closed(), min(60, self.options.playback_timeout)
            )
        except asyncio.TimeoutError:
            await self.stop("drain_timeout")

    async def start(self, *, autoplay: bool = True) -> None:
        if self.state != "READY":
            raise InvalidTransition("start requires READY")
        self._autoplay = autoplay
        self.state = "RUNNING"
        self._task = asyncio.create_task(
            self._run(), name=f"echuu:{self.inputs.session_id}"
        )

    def submit_interaction(self, interaction: Interaction) -> bool:
        """非阻塞提交；重复事件返回 False，容量耗尽显式报错。"""
        if self.state not in {"READY", "RUNNING"} or not self._accepting:
            raise InvalidTransition("Session is not accepting interactions")
        key = (interaction.source, interaction.user_id, interaction.event_id)
        if key in self._seen:
            return False
        if (
            len(self._seen) >= self.options.max_interactions
            or self._interactions.full()
        ):
            raise CapacityExceeded("Interaction capacity reached")
        self._interactions.put_nowait(interaction)
        self._seen.add(key)
        self.metrics["interactions_accepted"] = float(len(self._seen))
        return True

    async def _tick(self) -> None:
        # Bounded ingestion, with no concurrent writes to the engine's live state.
        for _ in range(min(8, self._interactions.qsize())):
            await self._call(self._driver.submit, self._interactions.get_nowait())
        diagnostics = await self._call(self._driver.tick)
        for item in diagnostics:
            if self._queue.qsize() >= max(0, self.options.event_capacity - 4):
                break  # Only optional diagnostics may be coalesced/dropped.
            await self._emit("diagnostic", payload=item)

    async def _run(self) -> None:
        try:
            while self.state == "RUNNING":
                step: PerformanceStep | None
                await self._tick()
                if self._draining:
                    break
                if self._gift_pending:
                    batch, expires = self._gift_pending
                    self._gift_pending = None
                    if monotonic() >= expires:
                        self._gifts[batch.batch_id][1].set_result(False)
                        continue
                    assert isinstance(self._driver, GiftDriver)
                    self._gift_current = batch.batch_id
                    try:
                        step = await self._call(
                            self._driver.prepare_gift,
                            batch,
                            self._autoplay,
                            timeout=max(0.01, expires - monotonic()),
                        )
                    except SessionTimeout:
                        # The worker may still be running; close rather than race its state.
                        await self.stop("gift_preparation_timeout")
                        return
                    if monotonic() >= expires:
                        await self._call(
                            self._driver.finish_gift, batch.batch_id, False
                        )
                        self._gifts[batch.batch_id][1].set_result(False)
                        self._gift_current = None
                        continue
                elif not self._autoplay:
                    await asyncio.sleep(0.05)
                    continue
                else:
                    step = await self._call(self._driver.next_step)
                if self.state != "RUNNING" or step is None:
                    break
                if step.step_id in self._playback:
                    raise InvalidTransition("Driver produced a duplicate step_id")
                if step.payload.get("stage") == "closing":
                    self._accepting = False
                    while not self._interactions.empty():
                        self._interactions.get_nowait()
                self._pending = step
                self._playback[step.step_id] = "PREPARED"
                self._ack.clear()
                self.metrics.setdefault(
                    "first_step_seconds", monotonic() - self._started_at
                )
                await self._emit("step", step=step)
                deadline = monotonic() + min(
                    self.options.playback_timeout,
                    60 if self._gift_current else self.options.playback_timeout,
                )
                while not self._ack.is_set() and self.state == "RUNNING":
                    if monotonic() >= deadline:
                        raise SessionTimeout("Host playback acknowledgement timed out")
                    await self._tick()
                    try:
                        await asyncio.wait_for(self._ack.wait(), 0.1)
                    except asyncio.TimeoutError:
                        pass
                if self.state != "RUNNING":
                    break
                confirmed = (
                    self._playback[step.step_id] == "PLAYED" and step in self._played
                )
                if confirmed:
                    await self._call(self._driver.played, step)
                if self._gift_current:
                    assert isinstance(self._driver, GiftDriver)
                    key = self._gift_current
                    await self._call(self._driver.finish_gift, key, confirmed)
                    future = self._gifts[key][1]
                    if not future.done():
                        future.set_result(confirmed)
                    self._gift_current = None
                await self._refresh_artifacts()
                self._pending = None
            await self.stop("completed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — provider failures terminate the session without logging private payloads
            logger.error("Session failed: %s", type(exc).__name__)
            await self.stop(type(exc).__name__)

    async def report_playback(
        self,
        step_id: str,
        status: Literal["COMMITTED", "PLAYED", "CANCELLED"],
        *,
        estimated: bool = False,
    ) -> bool:
        previous = self._playback.get(step_id)
        if previous == status:
            return False
        if (
            self.state != "RUNNING"
            or self._pending is None
            or self._pending.step_id != step_id
        ):
            raise InvalidTransition("Unknown, completed, or stopped playback step")
        allowed = {
            "PREPARED": {"COMMITTED", "CANCELLED"},
            "COMMITTED": {"PLAYED", "CANCELLED"},
        }
        if status not in allowed.get(previous or "", set()):
            raise InvalidTransition(
                f"Invalid playback transition: {previous} -> {status}"
            )
        self._playback[step_id] = status
        if status == "PLAYED":
            (self._estimated if estimated else self._played).append(self._pending)
        if status in {"PLAYED", "CANCELLED"}:
            self._ack.set()
        await self._emit(
            "playback",
            payload={"step_id": step_id, "status": status, "estimated": estimated},
        )
        return True

    async def events(self) -> AsyncIterator[SessionEvent]:
        if self._consumer:
            raise InvalidTransition("Only one event consumer is supported")
        self._consumer = True
        while True:
            if not self._queue.empty():
                event = self._queue.get_nowait()
                if event.type == "step" and self.state == "CLOSED":
                    continue
                yield event
                continue
            if self._closed.is_set():
                assert self._terminal is not None
                yield self._terminal
                return
            get = asyncio.create_task(self._queue.get())
            closed = asyncio.create_task(self._closed.wait())
            try:
                done, _ = await asyncio.wait(
                    {get, closed}, return_when=asyncio.FIRST_COMPLETED
                )
                if get in done:
                    yield get.result()
            finally:
                for task in (get, closed):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(get, closed, return_exceptions=True)

    async def stop(self, reason: str = "stopped") -> None:
        """立即封闭会话；运行中的同步调用不能强杀，其迟到结果不会发布。"""
        if self.state in {"STOPPING", "CLOSED"}:
            return
        self.state, self.reason = "STOPPING", reason
        self._accepting = False
        for key, (_, future) in self._gifts.items():
            if not future.done():
                confirmed = bool(
                    key == self._gift_current and self._pending in self._played
                )
                future.set_result(confirmed)
        if self._pending and self._playback[self._pending.step_id] not in {
            "PLAYED",
            "CANCELLED",
        }:
            self._playback[self._pending.step_id] = "CANCELLED"
        self._ack.set()
        if self._task and self._task is not asyncio.current_task():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        # Cleanup is serialized after any still-running provider call. Never claim
        # Future.cancel() stopped its underlying thread.
        self._pool.submit(self._driver.close)
        self._pool.shutdown(wait=False)
        self.state = "CLOSED"
        self._terminal = self._event(
            "closed",
            payload={
                "reason": reason,
                "metrics": {key: value for key, value in self.metrics.items()},
                "played_steps": len(self._played),
            },
        )
        self._closed.set()
        if self._recorder:
            try:
                await asyncio.wait_for(
                    self._recorder.record(self._terminal),
                    self.options.operation_timeout,
                )
            except Exception as exc:  # noqa: BLE001 — shutdown must finish even when the host recorder fails
                logger.error("Terminal record failed: %s", type(exc).__name__)

    async def aclose(self) -> None:
        await self.stop()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
