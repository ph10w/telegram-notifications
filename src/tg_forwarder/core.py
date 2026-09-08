from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, StrEnum, auto

from .models import ForwardingJob, JobStatus, MessageKey, VoiceBlock


class SourceKind(StrEnum):
    GROUP = "group"
    SUPERGROUP = "supergroup"
    CHANNEL = "channel"

    @property
    def allows_blocks(self) -> bool:
        return self in {SourceKind.GROUP, SourceKind.SUPERGROUP}


class MessageAction(Enum):
    SKIP_NON_VOICE = auto()
    RECORD_NON_VOICE = auto()
    CLOSE_ON_NON_VOICE = auto()
    IGNORE_SHORT_VOICE = auto()
    START_BLOCK = auto()
    JOIN_BLOCK = auto()
    FORWARD_STANDALONE = auto()


class BlockCloseReason(Enum):
    TIMEOUT = auto()
    DIFFERENT_AUTHOR_OR_TARGET = auto()
    FORWARDED_MESSAGE = auto()
    BLOCKS_DISABLED = auto()


@dataclass(frozen=True, slots=True)
class ActiveBlock:
    author_key: str
    target_chat_id: int
    non_voice_count: int
    last_voice_at: datetime


@dataclass(frozen=True, slots=True)
class MessageFacts:
    is_voice: bool
    observed_at: datetime
    duration_seconds: float | None = None
    author_key: str | None = None
    is_forwarded: bool = False
    allows_blocks: bool = True
    duration_minimum_exempt: bool = False


@dataclass(frozen=True, slots=True)
class BlockDecision:
    action: MessageAction
    close_reason: BlockCloseReason | None = None


@dataclass(frozen=True, slots=True)
class BlockPolicy:
    minimum_voice_duration_seconds: float
    target_chat_id: int
    timeout: timedelta = timedelta(hours=4)
    maximum_non_voice_gap: int = 5

    def decide(
        self,
        message: MessageFacts,
        active: ActiveBlock | None,
    ) -> BlockDecision:
        close_reason: BlockCloseReason | None = None
        if (
            active is not None
            and message.observed_at - active.last_voice_at >= self.timeout
        ):
            close_reason = BlockCloseReason.TIMEOUT
            active = None

        if not message.allows_blocks:
            if active is not None:
                close_reason = BlockCloseReason.BLOCKS_DISABLED
                active = None
            if not message.is_voice:
                return BlockDecision(MessageAction.SKIP_NON_VOICE, close_reason)
            if self._is_below_minimum(message):
                return BlockDecision(MessageAction.IGNORE_SHORT_VOICE, close_reason)
            return BlockDecision(MessageAction.FORWARD_STANDALONE, close_reason)

        if not message.is_voice:
            if active is None:
                return BlockDecision(MessageAction.SKIP_NON_VOICE, close_reason)
            if active.non_voice_count + 1 >= self.maximum_non_voice_gap:
                return BlockDecision(MessageAction.CLOSE_ON_NON_VOICE, close_reason)
            return BlockDecision(MessageAction.RECORD_NON_VOICE, close_reason)

        if message.is_forwarded:
            if active is not None:
                close_reason = BlockCloseReason.FORWARDED_MESSAGE
            if self._is_below_minimum(message):
                return BlockDecision(MessageAction.IGNORE_SHORT_VOICE, close_reason)
            return BlockDecision(MessageAction.FORWARD_STANDALONE, close_reason)

        if message.author_key is None:
            raise ValueError("Voice-message facts require an author key.")

        if active is not None and (
            active.author_key != message.author_key
            or active.target_chat_id != self.target_chat_id
        ):
            close_reason = BlockCloseReason.DIFFERENT_AUTHOR_OR_TARGET
            active = None

        if active is not None:
            return BlockDecision(MessageAction.JOIN_BLOCK, close_reason)

        if self._is_below_minimum(message):
            return BlockDecision(MessageAction.IGNORE_SHORT_VOICE, close_reason)
        return BlockDecision(MessageAction.START_BLOCK, close_reason)

    def _is_below_minimum(self, message: MessageFacts) -> bool:
        if message.duration_minimum_exempt:
            return False
        duration = message.duration_seconds
        return (
            self.minimum_voice_duration_seconds > 0
            and duration is not None
            and duration < self.minimum_voice_duration_seconds
        )


@dataclass(frozen=True, slots=True)
class ResetSnapshot:
    jobs: tuple[ForwardingJob, ...]
    blocks: tuple[VoiceBlock, ...]


@dataclass(frozen=True, slots=True)
class ResetPlan:
    clear_all: bool
    clear_cursor_source_ids: tuple[int, ...]
    cursor_boundaries: tuple[tuple[int, int], ...]
    jobs: tuple[MessageKey, ...]
    block_ids: tuple[int, ...]
    target_message_ids: tuple[int, ...]
    unavailable_target_count: int


@dataclass(frozen=True, slots=True)
class ResetPolicy:
    target_chat_id: int

    def create_plan(
        self,
        snapshot: ResetSnapshot,
        boundaries: dict[int, int] | None = None,
        *,
        cutoff: datetime | None = None,
        source_ids: frozenset[int] | None = None,
    ) -> ResetPlan:
        if boundaries is None and source_ids is None:
            affected_jobs = snapshot.jobs
            affected_blocks = snapshot.blocks
            expanded_boundaries: dict[int, int] = {}
            clear_all = True
            clear_cursor_source_ids: tuple[int, ...] = ()
        elif boundaries is None:
            affected_jobs = tuple(
                job for job in snapshot.jobs if job.source_id in source_ids
            )
            affected_blocks = tuple(
                block for block in snapshot.blocks if block.source_id in source_ids
            )
            expanded_boundaries = {}
            clear_all = False
            clear_cursor_source_ids = tuple(sorted(source_ids))
        else:
            affected_blocks = self._affected_blocks(
                snapshot.blocks, boundaries, cutoff
            )
            expanded_boundaries = self._expand_boundaries(
                boundaries, affected_blocks
            )
            affected_block_ids = {block.id for block in affected_blocks}
            affected_jobs = tuple(
                job
                for job in snapshot.jobs
                if self._job_is_affected(
                    job,
                    expanded_boundaries,
                    affected_block_ids,
                    cutoff,
                )
            )
            affected_jobs = self._include_origin_aliases(
                snapshot.jobs,
                affected_jobs,
                source_ids,
            )
            clear_all = False
            clear_cursor_source_ids = ()

        affected_jobs = tuple(
            sorted(affected_jobs, key=lambda job: (job.source_id, job.message_id))
        )
        affected_blocks = tuple(sorted(affected_blocks, key=lambda block: block.id))

        target_ids: set[int] = set()
        unavailable_count = 0
        for job in affected_jobs:
            if job.status is not JobStatus.FORWARDED:
                continue
            if (
                job.target_chat_id == self.target_chat_id
                and job.target_message_id is not None
            ):
                target_ids.add(job.target_message_id)
            else:
                unavailable_count += 1
        for block in affected_blocks:
            if block.target_chat_id == self.target_chat_id:
                target_ids.add(block.header_message_id)
            else:
                unavailable_count += 1

        return ResetPlan(
            clear_all=clear_all,
            clear_cursor_source_ids=clear_cursor_source_ids,
            cursor_boundaries=tuple(sorted(expanded_boundaries.items())),
            jobs=tuple(
                MessageKey(job.source_id, job.message_id)
                for job in affected_jobs
            ),
            block_ids=tuple(sorted(block.id for block in affected_blocks)),
            target_message_ids=tuple(sorted(target_ids)),
            unavailable_target_count=unavailable_count,
        )

    @staticmethod
    def _affected_blocks(
        blocks: tuple[VoiceBlock, ...],
        boundaries: dict[int, int],
        cutoff: datetime | None,
    ) -> tuple[VoiceBlock, ...]:
        if cutoff is not None:
            return tuple(
                block
                for block in blocks
                if block.source_id in boundaries and block.last_voice_at >= cutoff
            )
        return tuple(
            block
            for block in blocks
            if block.source_id in boundaries
            and block.last_observed_message_id > boundaries[block.source_id]
        )

    @staticmethod
    def _expand_boundaries(
        boundaries: dict[int, int],
        blocks: tuple[VoiceBlock, ...],
    ) -> dict[int, int]:
        expanded = dict(boundaries)
        for block in blocks:
            expanded[block.source_id] = min(
                expanded[block.source_id],
                max(0, block.first_message_id - 1),
            )
        return expanded

    @staticmethod
    def _job_is_affected(
        job: ForwardingJob,
        boundaries: dict[int, int],
        affected_block_ids: set[int],
        cutoff: datetime | None,
    ) -> bool:
        boundary = boundaries.get(job.source_id)
        if boundary is None:
            return False
        if job.block_id in affected_block_ids:
            return True
        if cutoff is None:
            return job.message_id > boundary
        if job.source_message_at is not None:
            return job.source_message_at >= cutoff
        return job.message_id > boundary

    @staticmethod
    def _include_origin_aliases(
        all_jobs: tuple[ForwardingJob, ...],
        affected_jobs: tuple[ForwardingJob, ...],
        source_ids: frozenset[int] | None,
    ) -> tuple[ForwardingJob, ...]:
        affected_origins = {
            (job.origin_chat_id, job.origin_message_id, job.target_chat_id)
            for job in affected_jobs
            if job.origin_chat_id is not None
            and job.origin_message_id is not None
            and job.target_chat_id is not None
        }
        if not affected_origins:
            return affected_jobs
        affected_keys = {
            (job.source_id, job.message_id) for job in affected_jobs
        }
        return (
            *affected_jobs,
            *(
                job
                for job in all_jobs
                if (job.source_id, job.message_id) not in affected_keys
                and (source_ids is None or job.source_id in source_ids)
                and (
                    job.origin_chat_id,
                    job.origin_message_id,
                    job.target_chat_id,
                )
                in affected_origins
            ),
        )
