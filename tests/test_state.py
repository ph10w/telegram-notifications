import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tg_forwarder.core import ResetPlan, ResetPolicy
from tg_forwarder.state import StateStore


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp_dir.name) / "state.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def reset_plan(
        self,
        boundaries: dict[int, int] | None = None,
        *,
        cutoff: datetime | None = None,
        target_chat_id: int = -1009,
        source_ids: frozenset[int] | None = None,
    ) -> ResetPlan:
        return ResetPolicy(target_chat_id).create_plan(
            self.store.load_reset_snapshot(),
            boundaries,
            cutoff=cutoff,
            source_ids=source_ids,
        )

    def test_cursor_only_moves_forward(self) -> None:
        self.store.advance_cursor(-1001, 10)
        self.store.advance_cursor(-1001, 4)
        self.assertEqual(self.store.cursor(-1001), 10)

    def test_failed_job_can_be_completed(self) -> None:
        self.store.mark_pending(-1001, 7)
        self.store.mark_failed(-1001, 7, "temporary")
        self.assertEqual(len(self.store.pending_jobs()), 1)

        self.store.mark_pending(-1001, 7)
        self.store.mark_forwarded(-1001, 7)

        self.assertTrue(self.store.is_complete(-1001, 7))
        self.assertEqual(self.store.pending_jobs(), [])
        self.assertEqual(self.store.cursor(-1001), 7)

    def test_forwarded_origin_is_deduplicated_per_target_chat(self) -> None:
        self.store.mark_pending(
            -1001,
            7,
            origin_chat_id=-100777,
            origin_message_id=123,
        )
        self.store.mark_forwarded(
            -1001,
            7,
            target_chat_id=-1009,
            target_message_id=101,
            origin_chat_id=-100777,
            origin_message_id=123,
        )

        self.assertTrue(
            self.store.has_forwarded_origin(-100777, 123, -1009)
        )
        self.assertFalse(
            self.store.has_forwarded_origin(-100777, 123, -1008)
        )

    def test_returns_at_most_two_original_message_candidates(self) -> None:
        message_at = datetime(2026, 8, 3, 17, 55, 58, tzinfo=UTC)
        block = self.store.create_voice_block(
            -1001,
            "sender:42",
            "Max",
            -1009,
            100,
            10,
            message_at,
        )
        for message_id in (10, 11):
            self.store.mark_pending(
                -1001,
                message_id,
                block_id=block.id,
                message_at=message_at,
                is_forwarded=False,
                duration_seconds=300.0,
            )
            self.store.mark_forwarded(
                -1001,
                message_id,
                target_chat_id=-1009,
                target_message_id=message_id + 100,
                block_id=block.id,
                message_at=message_at,
                is_forwarded=False,
                duration_seconds=300.0,
            )

        self.assertEqual(
            self.store.matching_original_message_ids(
                -1001,
                -1009,
                message_at,
                "sender:42",
                300.0,
                12,
            ),
            (10, 11),
        )
        self.assertEqual(
            self.store.matching_original_message_ids(
                -1001,
                -1009,
                message_at,
                "sender:42",
                301.0,
                12,
            ),
            (),
        )
        self.assertTrue(self.store.has_forwarded_origin(-1001, 10, -1009))

    def test_reset_removes_cursors_and_forwarding_history(self) -> None:
        self.store.mark_pending(-1001, 7)
        self.store.mark_forwarded(-1001, 7)

        cursor_count, message_count = self.store.apply_reset_plan(self.reset_plan())

        self.assertEqual(cursor_count, 1)
        self.assertEqual(message_count, 1)
        self.assertEqual(self.store.cursor(-1001), 0)
        self.assertFalse(self.store.has_cursor(-1001))
        self.assertFalse(self.store.is_complete(-1001, 7))

    def test_full_reset_can_be_limited_to_one_source(self) -> None:
        for source_id, message_id in ((-1001, 7), (-1002, 8)):
            self.store.mark_pending(source_id, message_id)
            self.store.mark_forwarded(source_id, message_id)

        plan = self.reset_plan(source_ids=frozenset((-1001,)))
        cursor_count, message_count = self.store.apply_reset_plan(plan)

        self.assertEqual(cursor_count, 1)
        self.assertEqual(message_count, 1)
        self.assertFalse(self.store.has_cursor(-1001))
        self.assertFalse(self.store.is_complete(-1001, 7))
        self.assertTrue(self.store.has_cursor(-1002))
        self.assertTrue(self.store.is_complete(-1002, 8))

    def test_rewind_removes_only_jobs_after_each_boundary(self) -> None:
        for message_id in (5, 10, 15):
            self.store.mark_pending(-1001, message_id)
            self.store.mark_forwarded(-1001, message_id)
        self.store.mark_pending(-1002, 20)
        self.store.mark_forwarded(-1002, 20)

        plan = self.reset_plan({-1001: 10})
        cursor_count, message_count = self.store.apply_reset_plan(plan)

        self.assertEqual(cursor_count, 1)
        self.assertEqual(message_count, 1)
        self.assertEqual(self.store.cursor(-1001), 10)
        self.assertTrue(self.store.is_complete(-1001, 10))
        self.assertFalse(self.store.is_complete(-1001, 15))
        self.assertTrue(self.store.is_complete(-1002, 20))

    def test_rewind_never_advances_an_older_cursor(self) -> None:
        self.store.advance_cursor(-1001, 5)

        self.store.apply_reset_plan(self.reset_plan({-1001: 10}))

        self.assertEqual(self.store.cursor(-1001), 5)

    def test_rewind_stores_zero_as_an_explicit_cursor(self) -> None:
        self.store.apply_reset_plan(self.reset_plan({-1001: 0}))

        self.assertTrue(self.store.has_cursor(-1001))
        self.assertEqual(self.store.cursor(-1001), 0)

    def test_returns_only_target_messages_for_the_configured_chat(self) -> None:
        for message_id, target_chat_id, target_message_id in (
            (5, -1009, 101),
            (6, -1008, 102),
            (7, None, None),
        ):
            self.store.mark_pending(-1001, message_id)
            self.store.mark_forwarded(
                -1001,
                message_id,
                target_chat_id=target_chat_id,
                target_message_id=target_message_id,
            )

        plan = self.reset_plan()

        self.assertEqual(plan.target_message_ids, (101,))
        self.assertEqual(plan.unavailable_target_count, 2)

    def test_persists_and_updates_voice_block(self) -> None:
        block = self.store.create_voice_block(
            -1001,
            "sender:42",
            "Alice",
            -1009,
            100,
            10,
        )
        self.store.mark_pending(-1001, 10, block_id=block.id)
        count = self.store.mark_forwarded(
            -1001,
            10,
            target_chat_id=-1009,
            target_message_id=101,
            block_id=block.id,
        )
        for message_id in range(11, 15):
            self.store.note_non_voice(-1001, message_id)

        self.assertEqual(count, 1)
        active = self.store.active_voice_block(-1001)
        self.assertIsNotNone(active)
        self.assertEqual(active.non_voice_count, 4)

        database = Path(self.temp_dir.name) / "state.sqlite3"
        self.store.close()
        self.store = StateStore(database)
        persisted = self.store.active_voice_block(-1001)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.author_label, "Alice")
        self.assertEqual(persisted.voice_count, 1)

        self.store.note_non_voice(-1001, 15, close=True)
        self.assertIsNone(self.store.active_voice_block(-1001))

    def test_time_reset_uses_last_voice_at_for_complete_block(self) -> None:
        cutoff = datetime(2026, 8, 1, 12, tzinfo=UTC)
        block = self.store.create_voice_block(
            -1001,
            "sender:42",
            "Alice",
            -1009,
            100,
            10,
            cutoff - timedelta(hours=1),
        )
        for source_message_id, target_message_id, message_at in (
            (10, 101, cutoff - timedelta(hours=1)),
            (12, 102, cutoff + timedelta(hours=1)),
        ):
            self.store.mark_pending(
                -1001,
                source_message_id,
                block_id=block.id,
                message_at=message_at,
            )
            self.store.mark_forwarded(
                -1001,
                source_message_id,
                target_chat_id=-1009,
                target_message_id=target_message_id,
                block_id=block.id,
                message_at=message_at,
            )

        plan = self.reset_plan({-1001: 11}, cutoff=cutoff)
        cursor_count, history_count = self.store.apply_reset_plan(plan)

        self.assertEqual(dict(plan.cursor_boundaries), {-1001: 9})
        self.assertEqual(plan.target_message_ids, (100, 101, 102))
        self.assertEqual(plan.unavailable_target_count, 0)
        self.assertEqual((cursor_count, history_count), (1, 2))
        self.assertIsNone(self.store.voice_block(block.id))

    def test_time_reset_uses_stored_source_message_timestamp(self) -> None:
        cutoff = datetime(2026, 8, 1, 12, tzinfo=UTC)
        for message_id, target_message_id, message_at in (
            (5, 101, cutoff + timedelta(minutes=1)),
            (15, 102, cutoff - timedelta(minutes=1)),
        ):
            self.store.mark_pending(-1001, message_id, message_at=message_at)
            self.store.mark_forwarded(
                -1001,
                message_id,
                target_chat_id=-1009,
                target_message_id=target_message_id,
                message_at=message_at,
            )

        plan = self.reset_plan({-1001: 10}, cutoff=cutoff)
        _, history_count = self.store.apply_reset_plan(plan)

        self.assertEqual(plan.target_message_ids, (101,))
        self.assertEqual(plan.unavailable_target_count, 0)
        self.assertEqual(history_count, 1)
        self.assertFalse(self.store.is_complete(-1001, 5))
        self.assertTrue(self.store.is_complete(-1001, 15))

    def test_adds_tracking_columns_to_an_existing_database(self) -> None:
        database = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(database)
        connection.executescript(
            """
            CREATE TABLE forwarding_jobs (
                source_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, message_id)
            );

            CREATE TABLE voice_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                author_key TEXT NOT NULL,
                author_label TEXT NOT NULL,
                target_chat_id INTEGER NOT NULL,
                header_message_id INTEGER NOT NULL,
                first_message_id INTEGER NOT NULL,
                voice_count INTEGER NOT NULL DEFAULT 0,
                non_voice_count INTEGER NOT NULL DEFAULT 0,
                last_observed_message_id INTEGER NOT NULL,
                closed_at_message_id INTEGER,
                created_at TEXT NOT NULL
            );

            INSERT INTO voice_blocks(
                source_id, author_key, author_label, target_chat_id,
                header_message_id, first_message_id, last_observed_message_id,
                created_at
            ) VALUES (
                -1001, 'sender:42', 'Alice', -1009, 100, 10, 10,
                '2026-08-01T08:00:00+00:00'
            );
            """
        )
        connection.close()

        migrated = StateStore(database)
        migrated.close()
        connection = sqlite3.connect(database)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(forwarding_jobs)")
            }
            block_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(voice_blocks)")
            }
            last_voice_at = connection.execute(
                "SELECT last_voice_at FROM voice_blocks"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertIn("target_chat_id", columns)
        self.assertIn("target_message_id", columns)
        self.assertIn("block_id", columns)
        self.assertIn("source_message_at", columns)
        self.assertIn("author_key", columns)
        self.assertIn("author_label", columns)
        self.assertIn("origin_chat_id", columns)
        self.assertIn("origin_message_id", columns)
        self.assertIn("is_forwarded", columns)
        self.assertIn("last_voice_at", block_columns)
        self.assertEqual(last_voice_at, "2026-08-01T08:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
