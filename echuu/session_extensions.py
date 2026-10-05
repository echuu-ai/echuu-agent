from typing import Protocol, runtime_checkable

from pydantic import Field, JsonValue

from .session_types import Contract, PerformanceStep


class GiftItem(Contract):
    event_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    user_name: str = Field(min_length=1, max_length=255)
    gift_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=80)
    value: int = Field(ge=0)
    effect: str = Field(max_length=500)
    sentences: int = Field(ge=1, le=3)


class GiftBatchInput(Contract):
    batch_id: str = Field(min_length=1, max_length=128)
    items: list[GiftItem] = Field(min_length=1, max_length=150)
    start_within: float = Field(gt=0, le=30)


class SessionArtifacts(Contract):
    schema_version: int = 1
    script: dict[str, JsonValue] | None = None
    dossier: dict[str, JsonValue] | None = None
    memory: dict[str, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class ArtifactDriver(Protocol):
    def export_artifacts(self) -> SessionArtifacts: ...


@runtime_checkable
class GiftDriver(Protocol):
    def prepare_gift(
        self, batch: GiftBatchInput, allow_effect: bool
    ) -> PerformanceStep: ...
    def finish_gift(self, batch_id: str, played: bool) -> None: ...
