import unittest
from datetime import UTC, datetime, timedelta

from tg_forwarder.core import (
    ActiveBlock,
    BlockCloseReason,
    BlockPolicy,
    MessageAction,
    MessageFacts,
    ResetPolicy,
    ResetSnapshot,
)
from tg_forwarder.models import ForwardingJob, JobStatus, VoiceBlock


class BlockPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 1, 12, tzinfo=UTC)
        self.policy = BlockPolicy(
            minimum_voice_duration_seconds=10,
            target_chat_id=-1002,
        )

    def active_block(self, **changes: object) -> ActiveBlock:
        values = {
            "author_key": "sender:42",
            "target_chat_id": -1002,
            "non_voice_count": 0,
            "last_voice_at": self.now,
        }
        values.update(changes)
        return ActiveBlock(**values)

    def voice(self, **changes: object) -> MessageFacts:
        values = {
            "is_voice": True,
            "observed_at": self.now,
            "duration_seconds": 10.0,
            "author_key": "sender:42",
        }
        values.update(changes)
        return MessageFacts(**values)

    def test_short_voice_can_join_but_cannot_start_block(self) -> None:
        short = self.voice(duration_seconds=1.0)

        self.assertEqual(
            self.policy.decide(short, self.active_block()).action,
            MessageAction.JOIN_BLOCK,
        )
        self.assertEqual(
            self.policy.decide(short, None).action,
            MessageAction.IGNORE_SHORT_VOICE,
        )

    def test_exempt_author_short_voice_starts_block(self) -> None:
        short = self.voice(duration_seconds=1.0, duration_minimum_exempt=True)

        self.assertEqual(
            self.policy.decide(short, None).action,
            MessageAction.START_BLOCK,
        )

    def test_exempt_author_short_forwarded_voice_is_standalone(self) -> None:
        decision = self.policy.decide(
            self.voice(
                duration_seconds=1.0,
                is_forwarded=True,
                duration_minimum_exempt=True,
            ),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.FORWARD_STANDALONE)
        self.assertEqual(decision.close_reason, BlockCloseReason.FORWARDED_MESSAGE)

    def test_exempt_author_short_channel_voice_is_standalone(self) -> None:
        decision = self.policy.decide(
            self.voice(
                duration_seconds=1.0,
                allows_blocks=False,
                duration_minimum_exempt=True,
            ),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.FORWARD_STANDALONE)
        self.assertEqual(decision.close_reason, BlockCloseReason.BLOCKS_DISABLED)

    def test_exempt_author_short_voice_still_closes_other_authors_block(self) -> None:
        decision = self.policy.decide(
            self.voice(
                duration_seconds=1.0,
                author_key="sender:99",
                duration_minimum_exempt=True,
            ),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.START_BLOCK)
        self.assertEqual(
            decision.close_reason,
            BlockCloseReason.DIFFERENT_AUTHOR_OR_TARGET,
        )

    def test_forwarded_voice_is_standalone_and_closes_active_block(self) -> None:
        decision = self.policy.decide(
            self.voice(is_forwarded=True),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.FORWARD_STANDALONE)
        self.assertEqual(decision.close_reason, BlockCloseReason.FORWARDED_MESSAGE)

    def test_channel_voice_is_standalone_and_closes_active_block(self) -> None:
        decision = self.policy.decide(
            self.voice(allows_blocks=False),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.FORWARD_STANDALONE)
        self.assertEqual(decision.close_reason, BlockCloseReason.BLOCKS_DISABLED)

    def test_short_channel_voice_cannot_use_an_existing_block(self) -> None:
        decision = self.policy.decide(
            self.voice(allows_blocks=False, duration_seconds=1.0),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.IGNORE_SHORT_VOICE)
        self.assertEqual(decision.close_reason, BlockCloseReason.BLOCKS_DISABLED)

    def test_short_forwarded_voice_closes_block_without_being_forwarded(self) -> None:
        decision = self.policy.decide(
            self.voice(is_forwarded=True, duration_seconds=1.0),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.IGNORE_SHORT_VOICE)
        self.assertEqual(decision.close_reason, BlockCloseReason.FORWARDED_MESSAGE)

    def test_other_author_closes_before_starting_new_block(self) -> None:
        decision = self.policy.decide(
            self.voice(author_key="sender:99"), self.active_block()
        )

        self.assertEqual(decision.action, MessageAction.START_BLOCK)
        self.assertEqual(
            decision.close_reason,
            BlockCloseReason.DIFFERENT_AUTHOR_OR_TARGET,
        )

    def test_fifth_non_voice_message_closes_block(self) -> None:
        non_voice = MessageFacts(is_voice=False, observed_at=self.now)

        self.assertEqual(
            self.policy.decide(
                non_voice, self.active_block(non_voice_count=3)
            ).action,
            MessageAction.RECORD_NON_VOICE,
        )
        self.assertEqual(
            self.policy.decide(
                non_voice, self.active_block(non_voice_count=4)
            ).action,
            MessageAction.CLOSE_ON_NON_VOICE,
        )

    def test_four_hour_inactivity_closes_before_processing_message(self) -> None:
        decision = self.policy.decide(
            self.voice(observed_at=self.now + timedelta(hours=4)),
            self.active_block(),
        )

        self.assertEqual(decision.action, MessageAction.START_BLOCK)
        self.assertEqual(decision.close_reason, BlockCloseReason.TIMEOUT)


class ResetPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 8, 1, 12, tzinfo=UTC)
        self.policy = ResetPolicy(target_chat_id=-1009)

    def job(
        self,
        message_id: int,
        *,
        message_at: datetime | None,
        source_id: int = -1001,
        status: JobStatus = JobStatus.FORWARDED,
        block_id: int | None = None,
        target_chat_id: int | None = -1009,
        target_message_id: int | None = None,
        origin_chat_id: int | None = None,
        origin_message_id: int | None = None,
    ) -> ForwardingJob:
        return ForwardingJob(
            source_id=source_id,
            message_id=message_id,
            status=status,
            target_chat_id=target_chat_id,
            target_message_id=target_message_id,
            block_id=block_id,
            source_message_at=message_at,
            origin_chat_id=origin_chat_id,
            origin_message_id=origin_message_id,
        )

    def block(self) -> VoiceBlock:
        return VoiceBlock(
            id=1,
            source_id=-1001,
            author_key="sender:42",
            author_label="Alice",
            target_chat_id=-1009,
            header_message_id=100,
            first_message_id=10,
            voice_count=2,
            non_voice_count=0,
            last_observed_message_id=12,
            last_voice_at=self.cutoff + timedelta(minutes=1),
            closed_at_message_id=None,
        )

    def test_last_voice_in_window_selects_complete_block(self) -> None:
        snapshot = ResetSnapshot(
            jobs=(
                self.job(
                    10,
                    message_at=self.cutoff - timedelta(days=1),
                    block_id=1,
                    target_message_id=101,
                ),
                self.job(
                    12,
                    message_at=self.cutoff + timedelta(minutes=1),
                    block_id=1,
                    target_message_id=102,
                ),
            ),
            blocks=(self.block(),),
        )

        plan = self.policy.create_plan(
            snapshot, {-1001: 11}, cutoff=self.cutoff
        )

        self.assertEqual(dict(plan.cursor_boundaries), {-1001: 9})
        self.assertEqual(
            tuple((key.source_id, key.message_id) for key in plan.jobs),
            ((-1001, 10), (-1001, 12)),
        )
        self.assertEqual(plan.block_ids, (1,))
        self.assertEqual(plan.target_message_ids, (100, 101, 102))

    def test_reset_includes_jobs_with_the_same_forwarded_origin(self) -> None:
        snapshot = ResetSnapshot(
            jobs=(
                self.job(
                    15,
                    message_at=self.cutoff + timedelta(minutes=1),
                    target_message_id=101,
                    origin_chat_id=-100777,
                    origin_message_id=123,
                ),
                self.job(
                    8,
                    source_id=-1002,
                    status=JobStatus.IGNORED,
                    message_at=self.cutoff - timedelta(days=1),
                    origin_chat_id=-100777,
                    origin_message_id=123,
                ),
            ),
            blocks=(),
        )

        plan = self.policy.create_plan(
            snapshot,
            {-1001: 10},
            cutoff=self.cutoff,
        )

        self.assertEqual(
            tuple((key.source_id, key.message_id) for key in plan.jobs),
            ((-1002, 8), (-1001, 15)),
        )
        self.assertEqual(plan.target_message_ids, (101,))

    def test_source_limited_reset_does_not_include_aliases_from_other_sources(
        self,
    ) -> None:
        snapshot = ResetSnapshot(
            jobs=(
                self.job(
                    15,
                    message_at=self.cutoff + timedelta(minutes=1),
                    target_message_id=101,
                    origin_chat_id=-100777,
                    origin_message_id=123,
                ),
                self.job(
                    8,
                    source_id=-1002,
                    status=JobStatus.IGNORED,
                    message_at=self.cutoff - timedelta(days=1),
                    origin_chat_id=-100777,
                    origin_message_id=123,
                ),
            ),
            blocks=(),
        )

        plan = self.policy.create_plan(
            snapshot,
            {-1001: 10},
            cutoff=self.cutoff,
            source_ids=frozenset((-1001,)),
        )

        self.assertEqual(
            tuple((key.source_id, key.message_id) for key in plan.jobs),
            ((-1001, 15),),
        )
        self.assertEqual(plan.target_message_ids, (101,))

    def test_timestamp_is_primary_and_message_id_is_legacy_fallback(self) -> None:
        snapshot = ResetSnapshot(
            jobs=(
                self.job(
                    5,
                    message_at=self.cutoff + timedelta(minutes=1),
                    target_message_id=101,
                ),
                self.job(
                    15,
                    message_at=self.cutoff - timedelta(minutes=1),
                    target_message_id=102,
                ),
                self.job(16, message_at=None, target_message_id=103),
            ),
            blocks=(),
        )

        plan = self.policy.create_plan(
            snapshot, {-1001: 10}, cutoff=self.cutoff
        )

        self.assertEqual(
            tuple(key.message_id for key in plan.jobs),
            (5, 16),
        )
        self.assertEqual(plan.target_message_ids, (101, 103))


if __name__ == "__main__":
    unittest.main()
