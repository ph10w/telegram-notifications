import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

from tg_api.bot_gateway import BotChat, BotIdentity, RelaySession
from tg_api.telegram_gateway import TelegramGateway
from tg_setup.models import DialogInfo
from tg_setup.service import _load_dialogs


class TelegramGatewayTests(unittest.IsolatedAsyncioTestCase):
    def test_maps_gateway_dialogs_to_setup_models(self) -> None:
        dialogs = (
            SimpleNamespace(id=-1001, name="Group", is_group=True, is_channel=False, is_user=False),
            SimpleNamespace(id=-1002, name="Channel", is_group=False, is_channel=True, is_user=False),
            SimpleNamespace(id=3, name="User", is_group=False, is_channel=False, is_user=True),
        )
        result = _load_dialogs(dialogs)

        self.assertEqual(
            result,
            (
                DialogInfo(-1001, "Gruppe", "Group"),
                DialogInfo(-1002, "Kanal", "Channel"),
                DialogInfo(3, "Benutzer", "User"),
            ),
        )

    async def test_prepares_monitoring_with_one_account_and_relay_setup(self) -> None:
        account_client = SimpleNamespace(
            start=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(
                id=42, first_name="Alice", last_name="Example"
            )),
            get_input_entity=AsyncMock(return_value=object()),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = TelegramGateway(
                Path(temp_dir) / "session", 123, "secret",
                entity_cache_limit=250, bot_token="token", target_chat=-1002,
                client_factory=Mock(return_value=account_client),
            )
            gateway._bot_gateway = SimpleNamespace(
                start_relay=AsyncMock(return_value=RelaySession(
                    BotIdentity(9, "publisher_bot"), None
                ))
            )
            identity = await gateway.prepare_monitoring("+4912345")

        self.assertEqual(identity[1], 42)
        account_client.start.assert_awaited_once_with(phone="+4912345")
        account_client.get_input_entity.assert_awaited_once_with(9)

    async def test_prepares_reset_with_the_configured_target(self) -> None:
        account_client = SimpleNamespace(
            start=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42, first_name="Alice")),
            get_dialogs=AsyncMock(),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = TelegramGateway(
                Path(temp_dir) / "session", 123, "secret",
                entity_cache_limit=250, bot_token="token", target_chat=-1002,
                phone="+4912345", client_factory=Mock(return_value=account_client),
            )
            gateway._bot_gateway = SimpleNamespace(
                resolve_chat=AsyncMock(return_value=BotChat(-1002, "Target", None))
            )
            target_id = await gateway.login_resolve_chat(-1002)

        self.assertEqual(target_id, -1002)
        account_client.start.assert_awaited_once_with(phone="+4912345")
        account_client.get_dialogs.assert_awaited_once_with()
        gateway._bot_gateway.resolve_chat.assert_awaited_once_with(-1002)

    async def test_gateway_batches_account_message_deletions(self) -> None:
        account_client = MagicMock()
        account_client.delete_messages = AsyncMock()
        factory = Mock(return_value=account_client)
        with tempfile.TemporaryDirectory() as temp_dir:
            gateway = TelegramGateway(
                Path(temp_dir) / "session", 123, "secret",
                entity_cache_limit=250, client_factory=factory,
            )
            target = object()
            await gateway.delete_messages_in_batches(target, tuple(range(1, 102)))

        self.assertEqual(account_client.delete_messages.await_count, 2)
        self.assertEqual(
            account_client.delete_messages.await_args_list[0].args,
            (target, list(range(1, 101))),
        )
        self.assertTrue(account_client.delete_messages.await_args_list[0].kwargs["revoke"])
        self.assertEqual(
            account_client.delete_messages.await_args_list[1].args,
            (target, [101]),
        )


if __name__ == "__main__":
    unittest.main()
