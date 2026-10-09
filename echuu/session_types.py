from collections.abc import AsyncIterator
from concurrent.futures import Future
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SessionError(RuntimeError):
    """会话契约错误。"""


class InvalidTransition(SessionError):
    pass


class CapacityExceeded(SessionError):
    pass


class SessionTimeout(SessionError):
    pass


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SessionInput(Contract):
    session_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    persona: str
    topic: str = Field(min_length=1)
    background: str = ""
    memory: str = ""
    language: str = "zh"
    persona_card: dict[str, JsonValue] | None = None
    max_steps: int = Field(default=15, ge=1, le=1000)


class SessionOptions(Contract):
    preparation_timeout: float = Field(default=100, gt=0)
    operation_timeout: float = Field(default=60, gt=0)
    playback_timeout: float = Field(default=120, gt=0)
    event_capacity: int = Field(default=32, ge=2)
    interaction_capacity: int = Field(default=64, ge=1)
    max_interactions: int = Field(default=4096, ge=1)
    rundown: bool = True
    grounding: bool = True


class Interaction(Contract):
    event_id: str = Field(min_length=1)
    source: str = Field(default="host", min_length=1)
    user_id: str = Field(min_length=1)
    user_name: str = "观众"
    text: str = Field(max_length=4000)
    kind: Literal["chat", "gift", "collect", "tangent"] = "chat"
    gift_id: str = ""
    amount: int = Field(default=0, ge=0)
    entities: list[dict[str, JsonValue]] = Field(default_factory=list)


class AudioDescriptor(Contract):
    media_type: Literal["audio/wav"] = "audio/wav"
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration: float = Field(ge=0)


class PerformanceStep(Contract):
    step_id: str
    line_id: str
    chunk_index: int = 0
    revision: int = 0
    speech: str
    payload: dict[str, JsonValue]
    audio: AudioDescriptor | None = None
    audio_data: bytes | None = Field(default=None, exclude=True, repr=False)


class SessionEvent(Contract):
    schema_version: Literal[1] = 1
    event_id: str
    session_id: str
    sequence: int
    type: Literal["ready", "step", "playback", "diagnostic", "closed"]
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    step: PerformanceStep | None = None


class SpeechProvider(Protocol):
    enabled: bool

    def prepare(self, text: str) -> Future[bytes]: ...
    def synthesize(self, text: str) -> bytes | None: ...
    def update_session(
        self, *, pitch_rate: float = 1, speech_rate: float = 1, volume: int = 50
    ) -> None: ...
    def set_instruction(self, instruction: str) -> None: ...
    def fork_preparation(self) -> "SpeechProvider": ...
    def close_preparation(self) -> None: ...


class SessionRecorder(Protocol):
    async def record(self, event: SessionEvent) -> None: ...


class SessionDriver(Protocol):
    """同步引擎适配契约；门面确保全部方法在同一专用线程串行执行。"""

    def prepare(self) -> list[str]: ...
    def next_step(self) -> PerformanceStep | None: ...
    def submit(self, interaction: Interaction) -> None: ...
    def tick(self) -> list[dict[str, JsonValue]]: ...
    def played(self, step: PerformanceStep) -> None: ...
    def close(self) -> None: ...


class SessionHost(Protocol):
    def events(self) -> AsyncIterator[SessionEvent]: ...
