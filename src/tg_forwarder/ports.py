from datetime import datetime
from typing import Protocol

from .core import ResetPlan, ResetSnapshot
from .models import ForwardingJob, PendingJob, VoiceBlock


class MonitoringStateRepository(Protocol):
    def cursor(self, source_id: int) -> int: ...

    def has_cursor(self, source_id: int) -> bool: ...

    def advance_cursor(self, source_id: int, message_id: int) -> None: ...

    def voice_block(self, block_id: int) -> VoiceBlock | None: ...

    def active_voice_block(self, source_id: int) -> VoiceBlock | None: ...

    def create_voice_block(
        self,
        source_id: int,
        author_key: str,
        author_label: str,
        target_chat_id: int,
        header_message_id: int,
        first_message_id: int,
        first_message_at: datetime | None = None,
    ) -> VoiceBlock: ...

    def close_active_voice_block(self, source_id: int, message_id: int) -> None: ...

    def note_non_voice(
        self, source_id: int, message_id: int, *, close: bool = False
    ) -> None: ...

    def is_complete(self, source_id: int, message_id: int) -> bool: ...

    def forwarded_job(
        self, source_id: int, message_id: int
    ) -> ForwardingJob | None: ...

    def has_forwarded_origin(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        target_chat_id: int,
    ) -> bool: ...

    def matching_original_message_ids(
        self,
        source_id: int,
        target_chat_id: int,
        message_at: datetime,
        author_key: str,
        duration_seconds: float,
        before_message_id: int,
    ) -> tuple[int, ...]: ...

    def mark_pending(
        self,
        source_id: int,
        message_id: int,
        *,
        block_id: int | None = None,
        message_at: datetime | None = None,
        author_key: str | None = None,
        author_label: str | None = None,
        origin_chat_id: int | None = None,
        origin_message_id: int | None = None,
        is_forwarded: bool | None = None,
        duration_seconds: float | None = None,
    ) -> None: ...

    def mark_forwarded(
        self,
        source_id: int,
        message_id: int,
        *,
        target_chat_id: int | None = None,
        target_message_id: int | None = None,
        block_id: int | None = None,
        message_at: datetime | None = None,
        author_key: str | None = None,
        author_label: str | None = None,
        origin_chat_id: int | None = None,
        origin_message_id: int | None = None,
        is_forwarded: bool | None = None,
        duration_seconds: float | None = None,
    ) -> int | None: ...

    def mark_failed(self, source_id: int, message_id: int, error: str) -> None: ...

    def mark_ignored(
        self,
        source_id: int,
        message_id: int,
        reason: str,
        *,
        target_chat_id: int | None = None,
        message_at: datetime | None = None,
        author_key: str | None = None,
        author_label: str | None = None,
        origin_chat_id: int | None = None,
        origin_message_id: int | None = None,
        is_forwarded: bool | None = None,
        duration_seconds: float | None = None,
    ) -> None: ...

    def pending_jobs(self) -> list[PendingJob]: ...


class ResetStateRepository(Protocol):
    def load_reset_snapshot(self) -> ResetSnapshot: ...

    def apply_reset_plan(self, plan: ResetPlan) -> tuple[int, int]: ...

    def close(self) -> None: ...


class ResetTelegramGateway(Protocol):
    async def login_resolve_chat(self, target_reference: int | str) -> int: ...

    async def resolve_source(self, reference: int | str) -> int: ...

    async def boundary_before(
        self, reference: int | str, cutoff: datetime
    ) -> tuple[int, int]: ...

    async def delete_target_messages(self, message_ids: tuple[int, ...]) -> None: ...

    async def close(self) -> None: ...


class StateRepository(MonitoringStateRepository, ResetStateRepository, Protocol):
    pass
