"""High-level Bot API operations shared by the local applications."""

import asyncio
import secrets
from dataclasses import dataclass
from typing import Any

from .bot_api import BotIdentity, _TelegramBotTransport
from .errors import TelegramApiError

type ChatRef = int | str


@dataclass(frozen=True, slots=True)
class PrivateChatSetup:
    identity: BotIdentity
    start_parameter: str
    update_offset: int | None

    @property
    def start_url(self) -> str:
        return f"https://t.me/{self.identity.username}?start={self.start_parameter}"


@dataclass(frozen=True, slots=True)
class BotChat:
    id: int
    title: str
    username: str | None


@dataclass(frozen=True, slots=True)
class RelaySession:
    identity: BotIdentity
    update_offset: int | None


class TelegramBotGateway:
    """Expose semantic Bot API operations without leaking the HTTP transport."""

    def __init__(self, token: str, target_chat: ChatRef | None = None) -> None:
        self._transport = _TelegramBotTransport(token)
        self._target_chat = target_chat

    async def send_text(self, text: str) -> None:
        await asyncio.to_thread(
            self._transport.send_text, self._require_target_chat(), text
        )

    async def start_relay(self) -> RelaySession:
        return await asyncio.to_thread(self._start_relay)

    async def resolve_chat(self, reference: ChatRef) -> BotChat:
        return await asyncio.to_thread(self._resolve_chat, reference)

    async def poll_message_updates(
        self, offset: int | None, timeout_seconds: int
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self._transport.call,
            "getUpdates",
            offset=offset,
            timeout=timeout_seconds,
            allowed_updates=["message"],
        ) or []

    async def delete_target_messages(self, message_ids: tuple[int, ...]) -> None:
        for index in range(0, len(message_ids), 100):
            await asyncio.to_thread(
                self._transport.call,
                "deleteMessages",
                chat_id=self._require_target_chat(),
                message_ids=message_ids[index:index + 100],
            )

    async def send_target_message(self, text: str) -> int:
        result = await asyncio.to_thread(
            self._transport.call,
            "sendMessage",
            chat_id=self._require_target_chat(),
            text=text,
            disable_notification=False,
        )
        return self._message_id(result)

    async def edit_target_message(self, message_id: int, text: str) -> None:
        await asyncio.to_thread(
            self._transport.call,
            "editMessageText",
            chat_id=self._require_target_chat(),
            message_id=message_id,
            text=text,
        )

    async def edit_target_caption(
        self, message_id: int, caption: str, entities: list[dict[str, object]] | None
    ) -> None:
        await asyncio.to_thread(
            self._transport.call,
            "editMessageCaption",
            chat_id=self._require_target_chat(),
            message_id=message_id,
            caption=caption,
            caption_entities=entities,
        )

    async def copy_relay_message(
        self,
        user_id: int,
        message_id: int,
        *,
        caption: str | None,
        entities: list[dict[str, object]] | None,
        is_voice: bool,
    ) -> int:
        parameters: dict[str, object] = {
            "chat_id": self._require_target_chat(),
            "from_chat_id": user_id,
            "message_id": message_id,
            "disable_notification": False,
        }
        if is_voice:
            parameters["caption"] = caption
            parameters["caption_entities"] = entities
        result = await asyncio.to_thread(self._transport.call, "copyMessage", **parameters)
        return self._message_id(result)

    async def delete_private_message(self, user_id: int, message_id: int) -> None:
        await asyncio.to_thread(
            self._transport.call,
            "deleteMessage",
            chat_id=user_id,
            message_id=message_id,
        )

    def start_private_chat_setup(self) -> PrivateChatSetup:
        return PrivateChatSetup(
            identity=self._transport.identity(),
            start_parameter=secrets.token_urlsafe(18),
            update_offset=self._transport.latest_update_offset(),
        )

    def wait_for_private_start(
        self,
        setup: PrivateChatSetup,
        *,
        timeout_seconds: int,
        poll_timeout_seconds: int,
    ) -> int:
        return self._transport.wait_for_private_start(
            setup.start_parameter,
            offset=setup.update_offset,
            timeout_seconds=timeout_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
        )

    def ensure_can_publish(self, target_chat: ChatRef, bot_id: int) -> None:
        self._transport.ensure_can_publish(target_chat, bot_id)

    def _start_relay(self) -> RelaySession:
        webhook = self._transport.call("getWebhookInfo")
        if isinstance(webhook, dict) and webhook.get("url"):
            raise TelegramApiError(
                "Für den Relay-Bot ist ein Webhook konfiguriert; getUpdates kann "
                "nicht gleichzeitig verwendet werden."
            )
        identity = self._transport.identity()
        updates = self._transport.call("getUpdates", offset=-1, timeout=0, limit=1)
        offset = int(updates[-1]["update_id"]) + 1 if updates else None
        return RelaySession(identity, offset)

    def _resolve_chat(self, reference: ChatRef) -> BotChat:
        chat = self._transport.call("getChat", chat_id=reference)
        if not isinstance(chat, dict) or not isinstance(chat.get("id"), int):
            raise TelegramApiError("Telegram lieferte keinen gültigen Zielchat.")
        title = str(chat.get("title") or chat.get("username") or chat["id"])
        username = chat.get("username")
        return BotChat(chat["id"], title, str(username) if username else None)

    def _require_target_chat(self) -> ChatRef:
        if self._target_chat is None:
            raise TelegramApiError("Dieser Bot-Gateway hat keinen Zielchat konfiguriert.")
        return self._target_chat

    @staticmethod
    def _message_id(message: object) -> int:
        if not isinstance(message, dict) or not isinstance(message.get("message_id"), int):
            raise TelegramApiError("Telegram lieferte keine Ziel-Nachrichten-ID.")
        return message["message_id"]
