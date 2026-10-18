import io
import json
import logging
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import asdict
from datetime import datetime, timezone
from time import monotonic

from .config import EngineConfig
from .gift_runtime import GiftRuntime
from .session_extensions import SessionArtifacts
from .session_types import (
    AudioDescriptor,
    Interaction,
    PerformanceStep,
    SessionInput,
    SessionOptions,
)

logger = logging.getLogger(__name__)


class StorytellingDriver:
    """复用已有引擎、rundown 和后台互动；文件、传输、数据库留给宿主。"""

    def __init__(
        self,
        inputs: SessionInput,
        config: EngineConfig,
        options: SessionOptions,
        *,
        engine=None,
        prepared_story=None,
    ):
        self.inputs, self.config, self.options = inputs, config, options
        self.engine = engine
        self.prepared_story = prepared_story
        self._own_engine = engine is None
        self._generator = None
        self._first_body = None
        self._opening = deque()
        self._closing = None
        self._diagnostics = deque(maxlen=64)
        self._index = 0
        self._spoken = []
        self._rundown_tts = None
        self._brief = ""
        self._card = None
        self._ended = False
        self._gift_runtime = None
        self._original_script = None

    def _rundown(self, stage, tts):
        from .modes.rundown import produce_closing_events, produce_opening_events
        from .modes.topic_brief import merge_brief_into_background

        function = (
            produce_opening_events if stage == "opening" else produce_closing_events
        )
        args = {"spoken_lines": list(self._spoken)} if stage == "closing" else {}
        try:
            return function(
                self.engine,
                self.inputs.name,
                self.inputs.persona,
                self.engine.state.topic if stage == "closing" else self.inputs.topic,
                "storytelling",
                tts=tts,
                card=self._card,
                background=merge_brief_into_background(
                    self.inputs.background, self.inputs.topic, self._brief
                ),
                **args,
            )
        except Exception as exc:  # noqa: BLE001 — optional rundown failure retains the prepared body
            logger.error("Optional %s failed: %s", stage, type(exc).__name__)
            return []
        finally:
            tts.close_preparation()

    def prepare(self):
        from .core.persona_card import PersonaCard
        from .live.background_interactions import BackgroundInteractions
        from .live.engine import EchuuLiveEngine
        from .modes.topic_brief import merge_brief_into_background, produce_topic_brief

        if self.engine is None:
            self.engine = EchuuLiveEngine(config=self.config)
        e, value = self.engine, self.inputs
        e.defer_playback_history = True
        e.on_steering = self._diagnostics.append
        e.on_token_hunt = self._diagnostics.append
        self._card = PersonaCard.from_dict(value.persona_card)
        self._brief = (
            produce_topic_brief(e.llm, value.topic)
            if self.options.grounding and self.prepared_story is None else ""
        )
        background = f"【本次用户设定·故事前提】{value.background}\n本次主题：{value.topic}\n保留用户人设与明确虚构设定；现实产品和新闻若与最新有来源的事实冲突，应更正旧背景，不得默认把真实事件当成虚构。"
        if value.memory:
            background += (
                f"\n【历史记忆·低于本次设定，不能作为本次新经历】{value.memory}"
            )
        background = merge_brief_into_background(background, value.topic, self._brief)
        opening_pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="echuu-opening"
        )
        future = None
        deadline = monotonic() + self.options.preparation_timeout
        try:
            if self.options.rundown:
                future = opening_pool.submit(
                    self._rundown, "opening", e.tts.fork_preparation()
                )
            if self.prepared_story is not None:
                from .prepared_story import load_story
                load_story(e, self.prepared_story)
            else:
                e.setup(
                    name=value.name,
                    persona=value.persona,
                    topic=value.topic,
                    background=background,
                    language=value.language,
                    persona_card=value.persona_card,
                    on_phase_callback=lambda _: self._diagnostics.append(
                        {"type": "reasoning", "content": "正在准备内容"}
                    ),
                )
            if future:
                try:
                    self._opening.extend(
                        future.result(timeout=max(0.01, min(2, deadline - monotonic())))
                    )
                except FutureTimeout:
                    # The worker owns and closes its fork even if its result is omitted.
                    logger.info("Optional opening missed its deadline")
        finally:
            opening_pool.shutdown(wait=False)
        self._original_script = json.loads(
            json.dumps(asdict(e.state.show), default=str)
        )
        self._original_script["metadata"] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            "name": value.name,
            "topic": value.topic,
            "total_units": len(e.state.show.units),
            "total_lines": sum(len(unit.lines) for unit in e.state.show.units),
        }
        e.background_interactions = BackgroundInteractions(e)
        self._gift_runtime = GiftRuntime(e)
        # Render lazily: one prepared step at a time, never enqueue an entire show.
        # Rewrites retain the existing exact-text preparation/commit mechanism.
        self._generator = e.run(
            max_steps=value.max_steps, play_audio=False, save_audio=False
        )
        # One original step is ready before READY; do not enqueue a whole show.
        self._first_body = next(self._generator, None)
        if e.tts.enabled:
            self._opening = deque(item for item in self._opening if item.get("audio"))
        return [str(item.get("speech", "")) for item in self._opening] + [
            line.text for unit in e.state.show.units for line in unit.lines
        ][:24]

    def next_step(self):
        if self._opening:
            item = self._opening.popleft()
        elif self._first_body is not None:
            item, self._first_body = self._first_body, None
        elif not self._ended:
            item = next(self._generator, None)
            if item is None:
                self._ended = True
                self.engine.background_interactions.close()
                self._closing = iter(
                    self._rundown("closing", self.engine.tts.fork_preparation())
                    if self.options.rundown
                    else []
                )
                item = next(self._closing, None)
        else:
            item = next(self._closing, None)
        if item is None:
            return None
        return self._step(item)

    def _step(self, item):
        self._index += 1
        payload = dict(item)
        data = payload.pop("audio", None)
        descriptor = None
        if data:
            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getsampwidth() != 2:
                    raise ValueError("Session audio requires 16-bit PCM WAV")
                descriptor = AudioDescriptor(
                    sample_rate=wav.getframerate(),
                    channels=wav.getnchannels(),
                    duration=wav.getnframes() / wav.getframerate(),
                )
        line = payload.get("line") or {}
        step = PerformanceStep(
            step_id=f"{self.inputs.session_id}:step:{self._index}",
            line_id=line.get("id") or f"rundown-{self._index}",
            chunk_index=line.get("chunk_index", 0),
            revision=self.engine.background_interactions.revision,
            speech=str(payload.get("speech", "")),
            payload=payload,
            audio=descriptor,
            audio_data=data,
        )
        return self._gift_runtime.annotate(step) if self._gift_runtime else step

    @property
    def gift_diagnostics(self):
        return self._gift_runtime.snapshot() if self._gift_runtime else []

    def export_artifacts(self):
        state = self.engine.state
        dossier = getattr(self.engine, "dossier", None)
        return SessionArtifacts(
            script=self._original_script,
            dossier=json.loads(json.dumps(asdict(dossier), default=str))
            if dossier
            else None,
            memory={
                "current_topic": state.topic,
                "candidate_promises": json.loads(
                    json.dumps(
                        getattr(getattr(state, "memory", None), "promises", []),
                        default=str,
                    )
                ),
                "topic_history": json.loads(
                    json.dumps(getattr(state, "topic_history", []), default=str)
                ),
                "interaction_history": json.loads(
                    json.dumps(getattr(state, "interaction_history", []), default=str)
                ),
            },
        )

    def prepare_gift(self, batch, allow_effect):
        speech, audio = self._gift_runtime.prepare(batch, allow_effect)
        return self._step(
            {
                "speech": speech,
                "audio": audio,
                "purpose": "gift_ack",
                "batch_id": batch.batch_id,
                "event_ids": [x.event_id for x in batch.items],
            }
        )

    def finish_gift(self, batch_id, played):
        return self._gift_runtime.finish(batch_id, played)

    def submit(self, interaction: Interaction):
        from .live.state import Danmaku
        from .live.story_tokens import compose_tangent_text, normalize_token

        if self._ended:
            raise ValueError("Session is closing")
        if len(self.engine.state.danmaku_queue) >= self.options.interaction_capacity:
            from .session_types import CapacityExceeded

            raise CapacityExceeded("Engine interaction queue is full")
        dm = Danmaku.from_input(
            interaction.text,
            user=interaction.user_name,
            kind=interaction.kind,
            gift_id=interaction.gift_id,
            amount=interaction.amount,
            client_id=interaction.event_id,
            entities=interaction.entities,
        )
        # Stable identity participates in engine dedup without conflating same-name viewers.
        dm.id = f"{interaction.source}:{interaction.user_id}:{interaction.event_id}"
        if dm.kind == "collect":
            token = normalize_token(dm.entities[0] if dm.entities else None, dm.text)
            if token:
                self.engine.collect_story_token(token)
            return
        if dm.kind == "tangent":
            card = self.engine.consume_tangent_card()
            if not card:
                raise ValueError("No tangent card is ready")
            dm.entities, dm.text = card, compose_tangent_text(card)
        self.engine.state.danmaku_queue.append(dm)
        self.engine.emit_steering(dm, "queued")

    def tick(self):
        if self._gift_runtime:
            self._gift_runtime.poll()
        if not self._ended:
            self.engine.background_interactions.tick()
        diagnostics = list(self._diagnostics)
        self._diagnostics.clear()
        return diagnostics

    def played(self, step: PerformanceStep):
        if self._gift_runtime:
            self._gift_runtime.played(step)
        self._spoken.append(step.speech)
        self.engine.state.spoken_history = [
            *self.engine.state.spoken_history,
            {
                "id": step.line_id,
                "text": step.speech,
                "kind": "played",
                "step_id": step.step_id,
            },
        ][-200:]

    def close(self):
        if self._gift_runtime:
            self._gift_runtime.close()
        if self._generator:
            self._generator.close()
        if self.engine:
            if getattr(self.engine, "background_interactions", None):
                self.engine.background_interactions.close()
            if self._own_engine:
                self.engine.tts.close_preparation()
                close_client = getattr(
                    getattr(self.engine.llm, "client", None), "close", None
                )
                if callable(close_client):
                    close_client()
            self.engine.on_steering = self.engine.on_token_hunt = None
