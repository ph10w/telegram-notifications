import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon import helpers, utils
from telethon.tl.types import Channel, Chat, PeerChannel, PeerUser

from tg_api.telegram_gateway import (
    BotTarget,
    ResolvedChat,
    ResolvedChats,
    TelegramGateway,
)

from tg_forwarder.app import (
    ResolvedSource,
    VoiceForwarder,
    linked_caption,
    message_author,
    source_kind,
    telegram_message_link,
)
from tg_forwarder.config import ForwarderConfig
from tg_forwarder.core import ResetPolicy, SourceKind
from tg_forwarder.models import JobStatus
from tg_forwarder.state import StateStore


def test_config(root: Path) -> ForwarderConfig:
    return ForwarderConfig(
        api_id=123,
        api_hash="secret",
        phone=None,
        session_path=root / "session",
        state_db=root / "state.sqlite3",
        log_level="INFO",
        entity_cache_limit=500,
        source_chats=(-1001,),
        target_chat=-1002,
        initial_scan_limit=100,
        min_voice_duration_seconds=0,
        include_video_notes=False,
        notification_bot_token="bot-secret",
    )


class VoiceForwarderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.state = StateStore(root / "state.sqlite3")
        self.client = SimpleNamespace(
            forward_messages=AsyncMock(return_value=SimpleNamespace(id=902)),
            send_file=AsyncMock(return_value=SimpleNamespace(id=901)),
            send_message=AsyncMock(return_value=SimpleNamespace(id=900)),
            edit_message=AsyncMock(),
            edit_caption=AsyncMock(),
        )
        self.forwarder = VoiceForwarder(
            self.client,
            test_config(root),
            self.state,
        )
        self.forwarder.target = object()
        self.forwarder.target_id = -1002

    def test_builds_client_with_configured_entity_cache_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = test_config(Path(temp_dir))
            with patch(
                "tg_api.telegram_gateway.TelegramClient"
            ) as client_class:
                TelegramGateway(
                    config.session_path,
                    config.api_id,
                    config.api_hash,
                    entity_cache_limit=config.entity_cache_limit,
                    phone=config.phone,
                )

        self.assertEqual(client_class.call_args.kwargs["entity_cache_limit"], 500)

    async def asyncTearDown(self) -> None:
        self.state.close()
        self.temp_dir.cleanup()

    async def test_forwards_voice_once(self) -> None:
        voice = SimpleNamespace(
            id=11,
            voice=object(),
            video_note=None,
            raw_text="Originaltext",
            entities=[],
            date=datetime(2026, 7, 16, 12, 50, 32, tzinfo=timezone.utc),
            post_author=None,
            sender_id=42,
            sender=SimpleNamespace(
                id=42,
                first_name="Alice",
                last_name="Example",
                username="alice",
            ),
        )

        await self.forwarder.process_message(-1001, voice)
        await self.forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()
        args, kwargs = self.client.send_file.await_args
        self.assertEqual(args, (self.forwarder.target, voice.voice))
        self.assertEqual(
            kwargs["caption"],
            "Originaltext\n\n🕒 16.07.2026 14:50:32",
        )
        self.assertEqual(kwargs["formatting_entities"][-1].url, "https://t.me/c/1/11")
        caption_with_surrogates = helpers.add_surrogate(kwargs["caption"])
        date_entity = kwargs["formatting_entities"][-1]
        self.assertEqual(
            helpers.del_surrogate(
                caption_with_surrogates[
                    date_entity.offset : date_entity.offset + date_entity.length
                ]
            ),
            "16.07.2026 14:50:32",
        )
        self.assertTrue(kwargs["voice_note"])
        self.client.send_message.assert_awaited_once_with(
            self.forwarder.target,
            "👤 Autor: Alice Example (@alice)\n🎙️ 1 Sprachnachricht",
            parse_mode=None,
        )
        self.client.edit_message.assert_not_awaited()
        self.client.forward_messages.assert_not_awaited()
        self.assertTrue(self.state.is_complete(-1001, 11))
        self.assertEqual(self.state.cursor(-1001), 11)
        reset_plan = ResetPolicy(-1002).create_plan(
            self.state.load_reset_snapshot()
        )
        self.assertEqual(reset_plan.target_message_ids, (900, 901))
        self.assertEqual(reset_plan.unavailable_target_count, 0)

    async def test_updates_forwarded_caption_after_source_edit(self) -> None:
        voice = SimpleNamespace(
            id=11,
            voice=object(),
            video_note=None,
            raw_text="",
            entities=[],
            date=datetime(2026, 7, 16, 12, 50, 32, tzinfo=timezone.utc),
            post_author=None,
            sender_id=42,
            sender=SimpleNamespace(
                id=42,
                first_name="Alice",
                last_name=None,
                username=None,
            ),
        )
        await self.forwarder.process_message(-1001, voice)
        voice.raw_text = "Später ergänzter Text"

        await self.forwarder.process_message_edit(-1001, voice)

        self.client.edit_caption.assert_awaited_once()
        args = self.client.edit_caption.await_args.args
        self.assertEqual(
            args[:3],
            (
                self.forwarder.target,
                901,
                "Später ergänzter Text\n\n🕒 16.07.2026 14:50:32",
            ),
        )
        self.assertEqual(args[3][-1].url, "https://t.me/c/1/11")

    def test_distinguishes_group_supergroup_and_channel_sources(self) -> None:
        group = Chat(
            id=1,
            title="Group",
            photo=None,
            participants_count=2,
            date=None,
            version=1,
        )
        supergroup = Channel(
            id=2,
            title="Supergroup",
            photo=None,
            date=None,
            megagroup=True,
        )
        channel = Channel(
            id=3,
            title="Channel",
            photo=None,
            date=None,
            broadcast=True,
        )

        self.assertIs(source_kind(group), SourceKind.GROUP)
        self.assertIs(source_kind(supergroup), SourceKind.SUPERGROUP)
        self.assertIs(source_kind(channel), SourceKind.CHANNEL)

    async def test_resolves_configured_chats_without_full_dialog_scan(self) -> None:
        target = Channel(
            id=2,
            title="Target",
            photo=None,
            date=None,
            broadcast=True,
        )
        source = Channel(
            id=1,
            title="Source",
            photo=None,
            date=None,
            megagroup=True,
        )
        self.client.resolve_target_and_sources = AsyncMock(
            return_value=ResolvedChats(
                BotTarget(-1002, "Target", None),
                (ResolvedChat(
                    -1000000000001, source, "Source", "supergroup"
                ),),
            )
        )

        await self.forwarder.resolve_chats()

        self.client.resolve_target_and_sources.assert_awaited_once_with(
            -1002, (-1001,)
        )
        self.assertIs(self.forwarder.sources[-1000000000001].kind, SourceKind.SUPERGROUP)

    async def test_channel_voices_are_forwarded_without_collection_block(self) -> None:
        source_entity = SimpleNamespace(username=None)
        self.forwarder.sources[-1001] = ResolvedSource(
            -1001,
            source_entity,
            "Channel",
            SourceKind.CHANNEL,
        )
        sender = SimpleNamespace(
            id=42,
            first_name="Alice",
            last_name=None,
            username=None,
        )

        def voice(message_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=10.0),
                raw_text="Kanaltext",
                entities=[],
                date=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
                post_author=None,
                sender_id=42,
                sender=sender,
            )

        await self.forwarder.process_message(-1001, voice(18))
        await self.forwarder.process_message(-1001, voice(19))

        self.client.send_message.assert_not_awaited()
        self.client.edit_message.assert_not_awaited()
        self.assertEqual(self.client.send_file.await_count, 2)
        self.assertIn(
            "👤 Autor: Alice\n🕒 01.08.2026 14:00:00",
            self.client.send_file.await_args.kwargs["caption"],
        )
        self.assertIsNone(self.state.active_voice_block(-1001))
        jobs = self.state.load_reset_snapshot().jobs
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(job.block_id is None for job in jobs))

    async def test_forwards_forwarded_voice_without_collection_block(self) -> None:
        received_at = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
        original_at = datetime(2026, 7, 16, 12, 50, 32, tzinfo=timezone.utc)
        original_sender = SimpleNamespace(
            id=77,
            first_name="Original",
            last_name="Alice",
            username="origin",
        )
        message = SimpleNamespace(
            id=15,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=30.0),
            raw_text="Weitergeleiteter Text",
            entities=[],
            date=received_at,
            fwd_from=SimpleNamespace(
                date=original_at,
                post_author=None,
                from_name=None,
                saved_from_name=None,
                saved_from_peer=None,
                saved_from_msg_id=None,
                from_id=PeerChannel(777),
                channel_post=123,
            ),
            forward=SimpleNamespace(
                sender=original_sender,
                chat=None,
                sender_id=77,
                chat_id=None,
            ),
            post_author=None,
            sender_id=42,
            sender=SimpleNamespace(
                id=42,
                first_name="Forwarder",
                last_name=None,
                username=None,
            ),
        )

        await self.forwarder.process_message(-1001, message)
        await self.forwarder.process_message(-1001, message)
        duplicate = SimpleNamespace(**vars(message))
        duplicate.id = 16
        await self.forwarder.process_message(-1003, duplicate)

        self.client.send_message.assert_not_awaited()
        self.client.edit_message.assert_not_awaited()
        self.client.send_file.assert_awaited_once()
        caption = self.client.send_file.await_args.kwargs["caption"]
        self.assertEqual(
            caption,
            "Weitergeleiteter Text\n\n"
            "👤 Autor: Original Alice (@origin)\n"
            "🕒 16.07.2026 14:50:32",
        )
        self.assertEqual(
            self.client.send_file.await_args.kwargs["formatting_entities"][-1].url,
            "https://t.me/c/1/15",
        )
        self.assertIsNone(self.state.active_voice_block(-1001))

        jobs = self.state.load_reset_snapshot().jobs
        self.assertEqual(len(jobs), 2)
        first = next(job for job in jobs if job.source_id == -1001)
        duplicate_job = next(job for job in jobs if job.source_id == -1003)
        self.assertEqual(first.block_id, None)
        self.assertEqual(first.source_message_at, original_at)
        self.assertEqual(first.author_key, "sender:77")
        self.assertEqual(first.author_label, "Original Alice (@origin)")
        expected_origin_chat_id = utils.get_peer_id(PeerChannel(777))
        self.assertEqual(first.origin_chat_id, expected_origin_chat_id)
        self.assertEqual(first.origin_message_id, 123)
        self.assertEqual(duplicate_job.status, JobStatus.IGNORED)
        self.assertEqual(duplicate_job.target_chat_id, -1002)
        self.assertEqual(duplicate_job.origin_chat_id, expected_origin_chat_id)
        self.assertEqual(duplicate_job.origin_message_id, 123)
        self.assertEqual(
            ResetPolicy(-1002).create_plan(
                self.state.load_reset_snapshot()
            ).target_message_ids,
            (901,),
        )

    async def test_deduplicates_internal_forward_without_telegram_origin_id(
        self,
    ) -> None:
        original_at = datetime(2026, 8, 3, 17, 55, 58, tzinfo=timezone.utc)
        sender = SimpleNamespace(
            id=77,
            first_name="Max",
            last_name=None,
            username=None,
        )
        original = SimpleNamespace(
            id=437101,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=300.0),
            raw_text="",
            entities=[],
            date=original_at,
            post_author=None,
            sender_id=77,
            sender=sender,
        )
        internal_forward = SimpleNamespace(
            id=437102,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=300.0),
            raw_text="",
            entities=[],
            date=datetime(2026, 8, 3, 17, 57, 46, tzinfo=timezone.utc),
            fwd_from=SimpleNamespace(
                date=original_at,
                post_author=None,
                from_name=None,
                saved_from_name=None,
                saved_from_peer=None,
                saved_from_msg_id=None,
                from_id=PeerUser(77),
                channel_post=None,
            ),
            forward=SimpleNamespace(
                sender=sender,
                chat=None,
                sender_id=77,
                chat_id=None,
            ),
            post_author=None,
            sender_id=77,
            sender=sender,
        )

        await self.forwarder.process_message(-1001, original)
        await self.forwarder.process_message(-1001, internal_forward)

        self.client.send_message.assert_awaited_once()
        self.client.send_file.assert_awaited_once()
        jobs = self.state.load_reset_snapshot().jobs
        duplicate = next(job for job in jobs if job.message_id == 437102)
        self.assertIs(duplicate.status, JobStatus.IGNORED)
        self.assertEqual(duplicate.target_chat_id, -1002)
        self.assertEqual(duplicate.origin_chat_id, -1001)
        self.assertEqual(duplicate.origin_message_id, 437101)

    async def test_keeps_internal_forward_when_origin_hint_is_ambiguous(self) -> None:
        original_at = datetime(2026, 8, 3, 17, 55, 58, tzinfo=timezone.utc)
        block = self.state.create_voice_block(
            -1001,
            "sender:77",
            "Max",
            -1002,
            800,
            437100,
            original_at,
        )
        for message_id in (437100, 437101):
            self.state.mark_pending(
                -1001,
                message_id,
                block_id=block.id,
                message_at=original_at,
                is_forwarded=False,
                duration_seconds=300.0,
            )
            self.state.mark_forwarded(
                -1001,
                message_id,
                target_chat_id=-1002,
                target_message_id=message_id,
                block_id=block.id,
                message_at=original_at,
                is_forwarded=False,
                duration_seconds=300.0,
            )
        sender = SimpleNamespace(
            id=77,
            first_name="Max",
            last_name=None,
            username=None,
        )
        internal_forward = SimpleNamespace(
            id=437102,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=300.0),
            raw_text="",
            entities=[],
            date=datetime(2026, 8, 3, 17, 57, 46, tzinfo=timezone.utc),
            fwd_from=SimpleNamespace(
                date=original_at,
                post_author=None,
                from_name=None,
                saved_from_name=None,
                saved_from_peer=None,
                saved_from_msg_id=None,
                from_id=PeerUser(77),
                channel_post=None,
            ),
            forward=SimpleNamespace(
                sender=sender,
                chat=None,
                sender_id=77,
                chat_id=None,
            ),
            post_author=None,
            sender_id=77,
            sender=sender,
        )

        await self.forwarder.process_message(-1001, internal_forward)

        self.client.send_file.assert_awaited_once()
        job = next(
            job
            for job in self.state.load_reset_snapshot().jobs
            if job.message_id == 437102
        )
        self.assertIs(job.status, JobStatus.FORWARDED)
        self.assertIsNone(job.origin_chat_id)
        self.assertIsNone(job.origin_message_id)

    async def test_does_not_dedupe_without_original_message_reference(self) -> None:
        original_at = datetime(2026, 7, 16, 12, 50, 32, tzinfo=timezone.utc)
        original_sender = SimpleNamespace(
            id=77,
            first_name="Original",
            last_name="Alice",
            username=None,
        )

        def forwarded(message_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=30.0),
                raw_text="",
                entities=[],
                date=datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
                fwd_from=SimpleNamespace(
                    date=original_at,
                    post_author=None,
                    from_name=None,
                    saved_from_name=None,
                    saved_from_peer=None,
                    saved_from_msg_id=None,
                    from_id=None,
                    channel_post=None,
                ),
                forward=SimpleNamespace(
                    sender=original_sender,
                    chat=None,
                    sender_id=77,
                    chat_id=None,
                ),
            )

        self.client.send_file.side_effect = (
            SimpleNamespace(id=901),
            SimpleNamespace(id=902),
        )
        await self.forwarder.process_message(-1001, forwarded(17))
        await self.forwarder.process_message(-1003, forwarded(18))

        self.assertEqual(self.client.send_file.await_count, 2)
        jobs = self.state.load_reset_snapshot().jobs
        self.assertTrue(all(job.status is JobStatus.FORWARDED for job in jobs))
        self.assertTrue(all(job.origin_chat_id is None for job in jobs))
        self.assertTrue(all(job.origin_message_id is None for job in jobs))

    async def test_catch_up_honors_an_explicit_zero_cursor(self) -> None:
        message = SimpleNamespace(id=1)
        iterator_arguments: dict[str, object] = {}

        async def iter_messages(entity: object, **kwargs: object):
            iterator_arguments["entity"] = entity
            iterator_arguments.update(kwargs)
            yield message

        source_entity = object()
        self.state.advance_cursor(-1001, 0)
        self.forwarder.sources[-1001] = ResolvedSource(-1001, source_entity, "Source")
        self.client.iter_messages = iter_messages
        self.forwarder.process_message = AsyncMock()
        self.forwarder._initial_scan = AsyncMock()

        await self.forwarder.catch_up()

        self.forwarder._initial_scan.assert_not_awaited()
        self.forwarder.process_message.assert_awaited_once_with(-1001, message)
        self.assertIs(iterator_arguments["entity"], source_entity)
        self.assertEqual(iterator_arguments["min_id"], 0)
        self.assertTrue(iterator_arguments["reverse"])

    async def test_skips_non_voice_message(self) -> None:
        text = SimpleNamespace(id=12, voice=None, video_note=None)

        await self.forwarder.process_message(-1001, text)

        self.client.forward_messages.assert_not_awaited()
        self.client.send_file.assert_not_awaited()
        self.assertEqual(self.state.cursor(-1001), 12)

    async def test_ignores_voice_shorter_than_configured_minimum(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=3.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        short_voice = SimpleNamespace(
            id=13,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=2.9),
        )

        await forwarder.process_message(-1001, short_voice)

        self.client.forward_messages.assert_not_awaited()
        self.client.send_file.assert_not_awaited()
        self.assertTrue(self.state.is_complete(-1001, 13))
        self.assertEqual(self.state.cursor(-1001), 13)

    async def test_forwards_voice_equal_to_configured_minimum(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=3.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        voice = SimpleNamespace(
            id=14,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=3.0),
        )

        await forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()

    async def test_exempt_author_short_voice_starts_block(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(
            test_config(root),
            min_voice_duration_seconds=10.0,
            duration_exempt_authors=frozenset({"sender:42", "username:bob"}),
        )
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        voice = SimpleNamespace(
            id=40,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=1.0),
            raw_text="",
            entities=[],
            date=None,
            post_author=None,
            sender_id=42,
            sender=SimpleNamespace(
                id=42,
                first_name="Alice",
                last_name=None,
                username=None,
            ),
        )

        await forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()
        self.client.send_message.assert_awaited_once()
        block = self.state.active_voice_block(-1001)
        self.assertIsNotNone(block)
        self.assertEqual(block.voice_count, 1)

    async def test_exempt_author_matches_username_without_sender_id(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(
            test_config(root),
            min_voice_duration_seconds=10.0,
            duration_exempt_authors=frozenset({"username:alice"}),
        )
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        voice = SimpleNamespace(
            id=41,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=1.0),
            raw_text="",
            entities=[],
            date=None,
            post_author=None,
            sender_id=None,
            sender=SimpleNamespace(
                id=None,
                first_name="Alice",
                last_name=None,
                username="Alice",
            ),
        )

        await forwarder.process_message(-1001, voice)

        self.client.send_file.assert_awaited_once()

    async def test_short_voice_from_other_author_stays_ignored(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(
            test_config(root),
            min_voice_duration_seconds=10.0,
            duration_exempt_authors=frozenset({"username:bob"}),
        )
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        voice = SimpleNamespace(
            id=42,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=1.0),
            raw_text="",
            entities=[],
            date=None,
            post_author=None,
            sender_id=42,
            sender=SimpleNamespace(
                id=42,
                first_name="Alice",
                last_name=None,
                username="alice",
            ),
        )

        await forwarder.process_message(-1001, voice)

        self.client.send_file.assert_not_awaited()
        self.client.send_message.assert_not_awaited()
        self.assertTrue(self.state.is_complete(-1001, 42))

    def test_builds_public_and_private_message_links(self) -> None:
        self.assertEqual(
            telegram_message_link(-1001806942431, 433238),
            "https://t.me/c/1806942431/433238",
        )
        self.assertEqual(
            telegram_message_link(-1001806942431, 433238, username="example"),
            "https://t.me/example/433238",
        )

    def test_linked_caption_keeps_link_with_long_text(self) -> None:
        message = SimpleNamespace(raw_text="x" * 1100, entities=[])

        caption, entities = linked_caption(message, "https://t.me/c/1/2")

        self.assertLessEqual(len(caption), 1024)
        self.assertTrue(caption.endswith("🕒 Ursprungsnachricht"))
        self.assertEqual(entities[-1].url, "https://t.me/c/1/2")

    def test_linked_caption_contains_original_date(self) -> None:
        message = SimpleNamespace(raw_text="Text", entities=[])

        caption, _ = linked_caption(
            message,
            "https://t.me/c/1/2",
            original_date="16.07.2026 14:50:32",
        )

        self.assertIn("🕒 16.07.2026 14:50:32", caption)

    async def test_same_author_extends_block_below_minimum_and_edits_count(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=10.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        sender = SimpleNamespace(
            id=42,
            first_name="Alice",
            last_name=None,
            username=None,
        )
        first = SimpleNamespace(
            id=20,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=10.0),
            raw_text="",
            entities=[],
            date=None,
            post_author=None,
            sender_id=42,
            sender=sender,
        )
        short_follow_up = SimpleNamespace(
            id=21,
            voice=object(),
            video_note=None,
            file=SimpleNamespace(duration=1.0),
            raw_text="",
            entities=[],
            date=None,
            post_author=None,
            sender_id=42,
            sender=sender,
        )

        await forwarder.process_message(-1001, first)
        await forwarder.process_message(-1001, short_follow_up)

        self.assertEqual(self.client.send_file.await_count, 2)
        self.client.send_message.assert_awaited_once()
        self.client.edit_message.assert_awaited_once_with(
            forwarder.target,
            900,
            "👤 Autor: Alice\n🎙️ 2 Sprachnachrichten",
            parse_mode=None,
        )
        block = self.state.active_voice_block(-1001)
        self.assertIsNotNone(block)
        self.assertEqual(block.voice_count, 2)

    async def test_short_voice_from_other_author_closes_block(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=10.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id

        def voice(message_id: int, sender_id: int, duration: float) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=duration),
                raw_text="",
                entities=[],
                date=None,
                post_author=None,
                sender_id=sender_id,
                sender=SimpleNamespace(
                    id=sender_id,
                    first_name=f"User {sender_id}",
                    last_name=None,
                    username=None,
                ),
            )

        await forwarder.process_message(-1001, voice(30, 1, 10.0))
        await forwarder.process_message(-1001, voice(31, 2, 1.0))
        await forwarder.process_message(-1001, voice(32, 1, 1.0))

        self.assertEqual(self.client.send_file.await_count, 1)
        self.assertTrue(self.state.is_complete(-1001, 31))
        self.assertTrue(self.state.is_complete(-1001, 32))
        self.assertIsNone(self.state.active_voice_block(-1001))

    async def test_eligible_voice_from_other_author_starts_new_block(self) -> None:
        self.client.send_message.side_effect = (
            SimpleNamespace(id=900),
            SimpleNamespace(id=903),
        )

        def voice(message_id: int, sender_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=10.0),
                raw_text="",
                entities=[],
                date=None,
                post_author=None,
                sender_id=sender_id,
                sender=SimpleNamespace(
                    id=sender_id,
                    first_name=f"User {sender_id}",
                    last_name=None,
                    username=None,
                ),
            )

        await self.forwarder.process_message(-1001, voice(35, 1))
        await self.forwarder.process_message(-1001, voice(36, 2))

        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.send_file.await_count, 2)
        block = self.state.active_voice_block(-1001)
        self.assertIsNotNone(block)
        self.assertEqual(block.author_key, "sender:2")
        self.assertEqual(block.header_message_id, 903)
        self.assertEqual(block.voice_count, 1)

    async def test_five_non_voice_messages_close_block(self) -> None:
        sender = SimpleNamespace(
            id=42,
            first_name="Alice",
            last_name=None,
            username=None,
        )

        def voice(message_id: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=1.0),
                raw_text="",
                entities=[],
                date=None,
                post_author=None,
                sender_id=42,
                sender=sender,
            )

        await self.forwarder.process_message(-1001, voice(40))
        for message_id in range(41, 45):
            await self.forwarder.process_message(
                -1001, SimpleNamespace(id=message_id, voice=None, video_note=None)
            )
        await self.forwarder.process_message(-1001, voice(45))
        self.assertEqual(self.client.send_message.await_count, 1)

        for message_id in range(46, 51):
            await self.forwarder.process_message(
                -1001, SimpleNamespace(id=message_id, voice=None, video_note=None)
            )
        self.client.send_message.return_value = SimpleNamespace(id=903)
        await self.forwarder.process_message(-1001, voice(51))

        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.send_file.await_count, 3)
        self.assertEqual(self.state.active_voice_block(-1001).first_message_id, 51)

    async def test_four_hours_since_last_voice_close_block(self) -> None:
        root = Path(self.temp_dir.name)
        config = replace(test_config(root), min_voice_duration_seconds=10.0)
        forwarder = VoiceForwarder(self.client, config, self.state)
        forwarder.target = self.forwarder.target
        forwarder.target_id = self.forwarder.target_id
        self.client.send_message.side_effect = (
            SimpleNamespace(id=900),
            SimpleNamespace(id=903),
        )
        sender = SimpleNamespace(
            id=42,
            first_name="Alice",
            last_name=None,
            username=None,
        )

        def voice(
            message_id: int, date: datetime, duration: float
        ) -> SimpleNamespace:
            return SimpleNamespace(
                id=message_id,
                voice=object(),
                video_note=None,
                file=SimpleNamespace(duration=duration),
                raw_text="",
                entities=[],
                date=date,
                post_author=None,
                sender_id=42,
                sender=sender,
            )

        first_at = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
        follow_up_at = first_at + timedelta(hours=3, minutes=59)
        block_age_four_hours = first_at + timedelta(hours=4)
        timeout_at = block_age_four_hours + timedelta(hours=4)

        await forwarder.process_message(-1001, voice(60, first_at, 10.0))
        await forwarder.process_message(-1001, voice(61, follow_up_at, 1.0))
        await forwarder.process_message(-1001, voice(62, block_age_four_hours, 1.0))
        await forwarder.process_message(-1001, voice(63, timeout_at, 10.0))

        self.assertEqual(self.client.send_file.await_count, 4)
        self.assertEqual(self.client.send_message.await_count, 2)
        self.assertEqual(self.client.edit_message.await_count, 2)
        self.assertEqual(
            self.client.edit_message.await_args_list[-1].args,
            (
                forwarder.target,
                900,
                "👤 Autor: Alice\n🎙️ 3 Sprachnachrichten",
            ),
        )
        self.assertEqual(
            self.client.edit_message.await_args_list[-1].kwargs,
            {"parse_mode": None},
        )
        active = self.state.active_voice_block(-1001)
        self.assertIsNotNone(active)
        self.assertEqual(active.first_message_id, 63)
        self.assertEqual(active.header_message_id, 903)

    async def test_uses_anonymous_admin_signature_as_author(self) -> None:
        message = SimpleNamespace(post_author="Redaktion", sender=None)

        author = await message_author(message)

        self.assertEqual(author, "Redaktion")


if __name__ == "__main__":
    unittest.main()
