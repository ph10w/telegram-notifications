from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    FAILED = "failed"
    FORWARDED = "forwarded"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class MessageKey:
    source_id: int
    message_id: int


@dataclass(frozen=True, slots=True)
class PendingJob:
    source_id: int
    message_id: int
    attempts: int
    block_id: int | None


@dataclass(frozen=True, slots=True)
class ForwardingJob:
    source_id: int
    message_id: int
    status: JobStatus
    target_chat_id: int | None
    target_message_id: int | None
    block_id: int | None
    source_message_at: datetime | None
    author_key: str | None = None
    author_label: str | None = None
    origin_chat_id: int | None = None
    origin_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class VoiceBlock:
    id: int
    source_id: int
    author_key: str
    author_label: str
    target_chat_id: int
    header_message_id: int
    first_message_id: int
    voice_count: int
    non_voice_count: int
    last_observed_message_id: int
    last_voice_at: datetime
    closed_at_message_id: int | None
