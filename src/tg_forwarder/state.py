import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .core import ResetPlan, ResetSnapshot
from .models import ForwardingJob, JobStatus, PendingJob, VoiceBlock


class StateStore:
    """Durable cursors and forwarding jobs used for restart-safe processing."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cursors (
                source_id INTEGER PRIMARY KEY,
                message_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS voice_blocks (
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
                last_voice_at TEXT,
                closed_at_message_id INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS forwarding_jobs (
                source_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'failed', 'forwarded', 'ignored')),
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                target_chat_id INTEGER,
                target_message_id INTEGER,
                block_id INTEGER,
                source_message_at TEXT,
                author_key TEXT,
                author_label TEXT,
                origin_chat_id INTEGER,
                origin_message_id INTEGER,
                is_forwarded INTEGER,
                duration_seconds REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_id, message_id)
            );
            """
        )
        columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(forwarding_jobs)")
        }
        if "target_chat_id" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN target_chat_id INTEGER"
            )
        if "target_message_id" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN target_message_id INTEGER"
            )
        if "block_id" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN block_id INTEGER"
            )
        if "source_message_at" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN source_message_at TEXT"
            )
        if "author_key" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN author_key TEXT"
            )
        if "author_label" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN author_label TEXT"
            )
        if "origin_chat_id" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN origin_chat_id INTEGER"
            )
        if "origin_message_id" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN origin_message_id INTEGER"
            )
        if "is_forwarded" not in columns:
            self._connection.execute(
                "ALTER TABLE forwarding_jobs ADD COLUMN is_forwarded INTEGER"
            )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS forwarding_jobs_origin_idx
            ON forwarding_jobs(
                origin_chat_id, origin_message_id, target_chat_id, status
            )
            """
        )
        block_columns = {
            str(row[1])
            for row in self._connection.execute("PRAGMA table_info(voice_blocks)")
        }
        if "last_voice_at" not in block_columns:
            self._connection.execute(
                "ALTER TABLE voice_blocks ADD COLUMN last_voice_at TEXT"
            )
        self._connection.execute(
            "UPDATE voice_blocks SET last_voice_at = created_at WHERE last_voice_at IS NULL"
        )
        self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _timestamp(value: datetime | None) -> str:
        if value is None:
            value = datetime.now(UTC)
        elif value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat()

    def close(self) -> None:
        self._connection.close()

    def cursor(self, source_id: int) -> int:
        row = self._connection.execute(
            "SELECT message_id FROM cursors WHERE source_id = ?", (source_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    def has_cursor(self, source_id: int) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM cursors WHERE source_id = ?", (source_id,)
        ).fetchone()
        return row is not None

    def advance_cursor(self, source_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )

    def load_reset_snapshot(self) -> ResetSnapshot:
        job_rows = self._connection.execute(
            """
            SELECT source_id, message_id, status, target_chat_id,
                   target_message_id, block_id, source_message_at,
                   author_key, author_label, origin_chat_id, origin_message_id
            FROM forwarding_jobs
            ORDER BY source_id, message_id
            """
        ).fetchall()
        block_rows = self._connection.execute(
            """
            SELECT id, source_id, author_key, author_label, target_chat_id,
                   header_message_id, first_message_id, voice_count,
                   non_voice_count, last_observed_message_id, last_voice_at,
                   closed_at_message_id
            FROM voice_blocks
            ORDER BY id
            """
        ).fetchall()
        blocks: list[VoiceBlock] = []
        for row in block_rows:
            block = self._voice_block(row)
            if block is not None:
                blocks.append(block)
        return ResetSnapshot(
            jobs=tuple(
                ForwardingJob(
                    source_id=int(row[0]),
                    message_id=int(row[1]),
                    status=JobStatus(str(row[2])),
                    target_chat_id=int(row[3]) if row[3] is not None else None,
                    target_message_id=int(row[4]) if row[4] is not None else None,
                    block_id=int(row[5]) if row[5] is not None else None,
                    source_message_at=(
                        datetime.fromisoformat(str(row[6]))
                        if row[6] is not None
                        else None
                    ),
                    author_key=str(row[7]) if row[7] is not None else None,
                    author_label=str(row[8]) if row[8] is not None else None,
                    origin_chat_id=int(row[9]) if row[9] is not None else None,
                    origin_message_id=int(row[10]) if row[10] is not None else None,
                )
                for row in job_rows
            ),
            blocks=tuple(blocks),
        )

    def apply_reset_plan(self, plan: ResetPlan) -> tuple[int, int]:
        with self._connection:
            if plan.clear_all:
                cursor_result = self._connection.execute("DELETE FROM cursors")
                jobs_result = self._connection.execute("DELETE FROM forwarding_jobs")
                self._connection.execute("DELETE FROM voice_blocks")
                return cursor_result.rowcount, jobs_result.rowcount

            cursors_deleted = 0
            for source_id in plan.clear_cursor_source_ids:
                result = self._connection.execute(
                    "DELETE FROM cursors WHERE source_id = ?", (source_id,)
                )
                cursors_deleted += result.rowcount
            jobs_deleted = 0
            for key in plan.jobs:
                result = self._connection.execute(
                    """
                    DELETE FROM forwarding_jobs
                    WHERE source_id = ? AND message_id = ?
                    """,
                    (key.source_id, key.message_id),
                )
                jobs_deleted += result.rowcount
            for block_id in plan.block_ids:
                self._connection.execute(
                    "DELETE FROM voice_blocks WHERE id = ?", (block_id,)
                )
            for source_id, boundary_id in plan.cursor_boundaries:
                self._connection.execute(
                    """
                    INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET
                        message_id = MIN(cursors.message_id, excluded.message_id)
                    """,
                    (source_id, boundary_id),
                )
        return cursors_deleted + len(plan.cursor_boundaries), jobs_deleted

    @staticmethod
    def _voice_block(row: sqlite3.Row | tuple[object, ...] | None) -> VoiceBlock | None:
        if row is None:
            return None
        return VoiceBlock(
            id=int(row[0]),
            source_id=int(row[1]),
            author_key=str(row[2]),
            author_label=str(row[3]),
            target_chat_id=int(row[4]),
            header_message_id=int(row[5]),
            first_message_id=int(row[6]),
            voice_count=int(row[7]),
            non_voice_count=int(row[8]),
            last_observed_message_id=int(row[9]),
            last_voice_at=datetime.fromisoformat(str(row[10])),
            closed_at_message_id=int(row[11]) if row[11] is not None else None,
        )

    def voice_block(self, block_id: int) -> VoiceBlock | None:
        row = self._connection.execute(
            """
            SELECT id, source_id, author_key, author_label, target_chat_id,
                   header_message_id, first_message_id, voice_count,
                   non_voice_count, last_observed_message_id, last_voice_at,
                   closed_at_message_id
            FROM voice_blocks
            WHERE id = ?
            """,
            (block_id,),
        ).fetchone()
        return self._voice_block(row)

    def active_voice_block(self, source_id: int) -> VoiceBlock | None:
        row = self._connection.execute(
            """
            SELECT id, source_id, author_key, author_label, target_chat_id,
                   header_message_id, first_message_id, voice_count,
                   non_voice_count, last_observed_message_id, last_voice_at,
                   closed_at_message_id
            FROM voice_blocks
            WHERE source_id = ? AND closed_at_message_id IS NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (source_id,),
        ).fetchone()
        return self._voice_block(row)

    def create_voice_block(
        self,
        source_id: int,
        author_key: str,
        author_label: str,
        target_chat_id: int,
        header_message_id: int,
        first_message_id: int,
        first_message_at: datetime | None = None,
    ) -> VoiceBlock:
        timestamp = self._timestamp(first_message_at)
        with self._connection:
            result = self._connection.execute(
                """
                INSERT INTO voice_blocks(
                    source_id, author_key, author_label, target_chat_id,
                    header_message_id, first_message_id,
                    last_observed_message_id, last_voice_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    author_key,
                    author_label,
                    target_chat_id,
                    header_message_id,
                    first_message_id,
                    first_message_id,
                    timestamp,
                    timestamp,
                ),
            )
        block = self.voice_block(int(result.lastrowid))
        if block is None:
            raise RuntimeError("Der angelegte Voice-Block konnte nicht geladen werden.")
        return block

    def close_active_voice_block(self, source_id: int, message_id: int) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE voice_blocks
                SET closed_at_message_id = ?,
                    last_observed_message_id = MAX(last_observed_message_id, ?)
                WHERE source_id = ? AND closed_at_message_id IS NULL
                """,
                (message_id, message_id, source_id),
            )

    def note_non_voice(
        self, source_id: int, message_id: int, *, close: bool = False
    ) -> None:
        """Persist a non-voice observation and optionally close its block."""
        block = self.active_voice_block(source_id)
        if block is None:
            return
        new_count = block.non_voice_count + 1
        with self._connection:
            self._connection.execute(
                """
                UPDATE voice_blocks
                SET non_voice_count = ?,
                    last_observed_message_id = MAX(last_observed_message_id, ?),
                    closed_at_message_id = CASE WHEN ? THEN ? ELSE NULL END
                WHERE id = ?
                """,
                (new_count, message_id, close, message_id, block.id),
            )

    def is_complete(self, source_id: int, message_id: int) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM forwarding_jobs
            WHERE source_id = ? AND message_id = ? AND status IN ('forwarded', 'ignored')
            """,
            (source_id, message_id),
        ).fetchone()
        return row is not None

    def forwarded_job(
        self, source_id: int, message_id: int
    ) -> ForwardingJob | None:
        row = self._connection.execute(
            """
            SELECT source_id, message_id, status, target_chat_id,
                   target_message_id, block_id, source_message_at,
                   author_key, author_label, origin_chat_id, origin_message_id
            FROM forwarding_jobs
            WHERE source_id = ? AND message_id = ? AND status = 'forwarded'
            """,
            (source_id, message_id),
        ).fetchone()
        if row is None:
            return None
        return ForwardingJob(
            source_id=int(row[0]),
            message_id=int(row[1]),
            status=JobStatus(str(row[2])),
            target_chat_id=int(row[3]) if row[3] is not None else None,
            target_message_id=int(row[4]) if row[4] is not None else None,
            block_id=int(row[5]) if row[5] is not None else None,
            source_message_at=(
                datetime.fromisoformat(str(row[6])) if row[6] is not None else None
            ),
            author_key=str(row[7]) if row[7] is not None else None,
            author_label=str(row[8]) if row[8] is not None else None,
            origin_chat_id=int(row[9]) if row[9] is not None else None,
            origin_message_id=int(row[10]) if row[10] is not None else None,
        )

    def has_forwarded_origin(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        target_chat_id: int,
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM forwarding_jobs
            WHERE target_chat_id = ?
              AND status = 'forwarded'
              AND (
                  (origin_chat_id = ? AND origin_message_id = ?)
                  OR (
                      source_id = ? AND message_id = ?
                      AND COALESCE(
                          is_forwarded,
                          CASE WHEN block_id IS NOT NULL THEN 0 END
                      ) = 0
                  )
              )
            LIMIT 1
            """,
            (
                target_chat_id,
                origin_chat_id,
                origin_message_id,
                origin_chat_id,
                origin_message_id,
            ),
        ).fetchone()
        return row is not None

    def matching_original_message_ids(
        self,
        source_id: int,
        target_chat_id: int,
        message_at: datetime,
        author_key: str,
        duration_seconds: float,
        before_message_id: int,
    ) -> tuple[int, ...]:
        rows = self._connection.execute(
            """
            SELECT jobs.message_id
            FROM forwarding_jobs AS jobs
            LEFT JOIN voice_blocks AS blocks ON blocks.id = jobs.block_id
            WHERE jobs.source_id = ?
              AND jobs.target_chat_id = ?
              AND jobs.message_id < ?
              AND jobs.source_message_at = ?
              AND jobs.duration_seconds = ?
              AND jobs.status = 'forwarded'
              AND COALESCE(jobs.author_key, blocks.author_key) = ?
              AND COALESCE(
                  jobs.is_forwarded,
                  CASE WHEN jobs.block_id IS NOT NULL THEN 0 END
              ) = 0
            ORDER BY jobs.message_id
            LIMIT 2
            """,
            (
                source_id,
                target_chat_id,
                before_message_id,
                self._timestamp(message_at),
                duration_seconds,
                author_key,
            ),
        ).fetchall()
        return tuple(int(row[0]) for row in rows)

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
    ) -> None:
        timestamp = self._timestamp(message_at) if message_at is not None else None
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO forwarding_jobs(
                    source_id, message_id, status, block_id,
                    source_message_at, author_key, author_label,
                    origin_chat_id, origin_message_id, is_forwarded,
                    duration_seconds, updated_at
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, message_id) DO UPDATE SET
                    status = 'pending',
                    block_id = excluded.block_id,
                    source_message_at = COALESCE(
                        excluded.source_message_at,
                        forwarding_jobs.source_message_at
                    ),
                    author_key = COALESCE(
                        excluded.author_key, forwarding_jobs.author_key
                    ),
                    author_label = COALESCE(
                        excluded.author_label, forwarding_jobs.author_label
                    ),
                    origin_chat_id = COALESCE(
                        excluded.origin_chat_id, forwarding_jobs.origin_chat_id
                    ),
                    origin_message_id = COALESCE(
                        excluded.origin_message_id,
                        forwarding_jobs.origin_message_id
                    ),
                    is_forwarded = COALESCE(
                        excluded.is_forwarded, forwarding_jobs.is_forwarded
                    ),
                    duration_seconds = COALESCE(
                        excluded.duration_seconds, forwarding_jobs.duration_seconds
                    ),
                    updated_at = excluded.updated_at
                WHERE forwarding_jobs.status NOT IN ('forwarded', 'ignored')
                """,
                (
                    source_id,
                    message_id,
                    block_id,
                    timestamp,
                    author_key,
                    author_label,
                    origin_chat_id,
                    origin_message_id,
                    is_forwarded,
                    duration_seconds,
                    self._now(),
                ),
            )
            if block_id is not None:
                self._connection.execute(
                    """
                    UPDATE voice_blocks
                    SET non_voice_count = 0,
                        last_observed_message_id = MAX(last_observed_message_id, ?)
                    WHERE id = ?
                    """,
                    (message_id, block_id),
                )

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
    ) -> int | None:
        with self._connection:
            result = self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'forwarded', attempts = attempts + 1,
                    last_error = NULL, target_chat_id = ?, target_message_id = ?,
                    block_id = ?,
                    source_message_at = COALESCE(?, source_message_at),
                    author_key = COALESCE(?, author_key),
                    author_label = COALESCE(?, author_label),
                    origin_chat_id = COALESCE(?, origin_chat_id),
                    origin_message_id = COALESCE(?, origin_message_id),
                    is_forwarded = COALESCE(?, is_forwarded),
                    duration_seconds = COALESCE(?, duration_seconds),
                    updated_at = ?
                WHERE source_id = ? AND message_id = ?
                  AND status NOT IN ('forwarded', 'ignored')
                """,
                (
                    target_chat_id,
                    target_message_id,
                    block_id,
                    self._timestamp(message_at) if message_at is not None else None,
                    author_key,
                    author_label,
                    origin_chat_id,
                    origin_message_id,
                    is_forwarded,
                    duration_seconds,
                    self._now(),
                    source_id,
                    message_id,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )
            if result.rowcount and block_id is not None:
                timestamp = self._timestamp(message_at)
                self._connection.execute(
                    """
                    UPDATE voice_blocks
                    SET voice_count = voice_count + 1,
                        non_voice_count = 0,
                        last_observed_message_id = MAX(last_observed_message_id, ?),
                        last_voice_at = MAX(last_voice_at, ?)
                    WHERE id = ?
                    """,
                    (message_id, timestamp, block_id),
                )
        block = self.voice_block(block_id) if block_id is not None else None
        return block.voice_count if block else None

    def mark_failed(self, source_id: int, message_id: int, error: str) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'failed', attempts = attempts + 1,
                    last_error = ?, updated_at = ?
                WHERE source_id = ? AND message_id = ?
                """,
                (error[:1000], self._now(), source_id, message_id),
            )

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
    ) -> None:
        self.mark_pending(
            source_id,
            message_id,
            message_at=message_at,
            author_key=author_key,
            author_label=author_label,
            origin_chat_id=origin_chat_id,
            origin_message_id=origin_message_id,
            is_forwarded=is_forwarded,
            duration_seconds=duration_seconds,
        )
        with self._connection:
            self._connection.execute(
                """
                UPDATE forwarding_jobs
                SET status = 'ignored', last_error = ?,
                    target_chat_id = COALESCE(?, target_chat_id), updated_at = ?
                WHERE source_id = ? AND message_id = ?
                """,
                (
                    reason[:1000],
                    target_chat_id,
                    self._now(),
                    source_id,
                    message_id,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO cursors(source_id, message_id) VALUES (?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    message_id = MAX(cursors.message_id, excluded.message_id)
                """,
                (source_id, message_id),
            )

    def pending_jobs(self) -> list[PendingJob]:
        rows = self._connection.execute(
            """
            SELECT source_id, message_id, attempts
                   , block_id
            FROM forwarding_jobs
            WHERE status IN ('pending', 'failed')
            ORDER BY updated_at ASC
            """
        ).fetchall()
        return [
            PendingJob(
                int(row[0]),
                int(row[1]),
                int(row[2]),
                int(row[3]) if row[3] is not None else None,
            )
            for row in rows
        ]
