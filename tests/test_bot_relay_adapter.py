import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from telethon.tl.types import MessageEntityTextUrl

from tg_api.bot_gateway import BotIdentity, RelaySession
from tg_api.telegram_gateway import TelegramGateway


def relay(account_client: object) -> TelegramGateway:
    with tempfile.TemporaryDirectory() as temp_dir:
        return TelegramGateway(
            Path(temp_dir) / "session", 123, "secret",
            entity_cache_limit=250, bot_token="token", target_chat=-1002,
            client_factory=Mock(return_value=account_client),
        )


class TelegramGatewayRelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_edits_voice_caption_in_target_chat(self) -> None:
        gateway = relay(Mock())
        gateway._bot_gateway = SimpleNamespace(edit_target_caption=AsyncMock())
        entity = MessageEntityTextUrl(offset=0, length=4, url="https://example.test")

        await gateway.edit_caption(object(), 88, "text", [entity])

        gateway._bot_gateway.edit_target_caption.assert_awaited_once_with(
            88, "text", [{
                "type": "text_link", "offset": 0, "length": 4,
                "url": "https://example.test",
            }]
        )

    async def test_starts_private_relay_with_its_own_account_client(self) -> None:
        account_client = SimpleNamespace(
            get_input_entity=AsyncMock(return_value=object()), get_entity=AsyncMock(),
        )
        gateway = relay(account_client)
        gateway._bot_gateway = SimpleNamespace(
            start_relay=AsyncMock(return_value=RelaySession(
                BotIdentity(9, "publisher_bot"), 42
            ))
        )

        await gateway.start_relay(123)

        account_client.get_input_entity.assert_awaited_once_with(9)
        account_client.get_entity.assert_not_awaited()
        self.assertEqual(gateway._user_id, 123)
        self.assertEqual(gateway._offset, 42)

    async def test_relays_existing_voice_and_deletes_staging_message(self) -> None:
        media = object()
        account_client = SimpleNamespace(
            send_file=AsyncMock(return_value=SimpleNamespace(id=55)),
            delete_messages=AsyncMock(),
        )
        gateway = relay(account_client)
        gateway._user_id = 123
        gateway._bot_entity = object()
        gateway._wait_for_relay = AsyncMock(return_value=77)
        gateway._bot_gateway = SimpleNamespace(
            copy_relay_message=AsyncMock(return_value=88),
            delete_private_message=AsyncMock(),
        )
        message = SimpleNamespace(voice=media, video_note=None)
        entities = [MessageEntityTextUrl(offset=0, length=4, url="https://example.test")]

        result = await gateway.copy_message(
            -1001, 10, message, caption="date", entities=entities
        )

        self.assertEqual(result.id, 88)
        account_client.send_file.assert_awaited_once()
        send_args, send_kwargs = account_client.send_file.await_args
        self.assertEqual(send_args, (gateway._bot_entity, media))
        self.assertTrue(send_kwargs["caption"].startswith("telegram-voice-forwarder:"))
        self.assertTrue(send_kwargs["silent"])
        self.assertTrue(send_kwargs["voice_note"])
        gateway._bot_gateway.copy_relay_message.assert_awaited_once_with(
            123, 77, caption="date", is_voice=True,
            entities=[{
                "type": "text_link", "offset": 0, "length": 4,
                "url": "https://example.test",
            }],
        )
        gateway._bot_gateway.delete_private_message.assert_awaited_once_with(123, 77)
        account_client.delete_messages.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
