from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import ChatRef, ForwarderConfig
from .core import ResetPolicy
from .ports import ResetStateRepository, ResetTelegramGateway


@dataclass(frozen=True, slots=True)
class ResetResult:
    state_db: Path
    cutoff: datetime | None
    cursor_count: int
    history_count: int
    deleted_target_count: int
    unavailable_target_count: int


async def reset_scan_state(
    config: ForwarderConfig,
    period: timedelta | None = None,
    *,
    source_chat: ChatRef | None = None,
    telegram: ResetTelegramGateway,
    state: ResetStateRepository,
    now: datetime | None = None,
) -> ResetResult:
    cutoff = (now or datetime.now(UTC)) - period if period is not None else None
    try:
        target_chat_id = await telegram.login_resolve_chat(config.target_chat)

        boundaries: dict[int, int] | None = None
        source_ids: frozenset[int] | None = None
        if cutoff is not None:
            boundaries = {}
            references = (
                (source_chat,) if source_chat is not None else config.source_chats
            )
            for reference in references:
                source_id, boundary = await telegram.boundary_before(
                    reference, cutoff
                )
                boundaries[source_id] = boundary
            if source_chat is not None:
                source_ids = frozenset(boundaries)
        elif source_chat is not None:
            source_ids = frozenset((await telegram.resolve_source(source_chat),))

        plan = ResetPolicy(target_chat_id).create_plan(
            state.load_reset_snapshot(),
            boundaries,
            cutoff=cutoff,
            source_ids=source_ids,
        )
        await telegram.delete_target_messages(plan.target_message_ids)

        cursor_count, history_count = state.apply_reset_plan(plan)

        return ResetResult(
            state_db=config.state_db,
            cutoff=cutoff,
            cursor_count=cursor_count,
            history_count=history_count,
            deleted_target_count=len(plan.target_message_ids),
            unavailable_target_count=plan.unavailable_target_count,
        )
    finally:
        state.close()
        await telegram.close()
