"""The single Telegram transport boundary for the application packages."""

import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from telethon import TelegramClient, events, helpers, utils
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import (
    Channel,
    Chat,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityCustomEmoji,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUrl,
)

from tg_api.bot_gateway import BotChat, TelegramBotGateway
from tg_api.errors import TelegramApiError

LOGGER = logging.getLogger(__name__)
RELAY_TIMEOUT_SECONDS = 45
type ChatRef = int | str
TelegramBotApiError = TelegramApiError
BotTarget = BotChat


@dataclass(frozen=True, slots=True)
class ResolvedChat:
    id: int
    entity: Any
    title: str
    kind: str


@dataclass(frozen=True, slots=True)
class ResolvedChats:
    target: BotChat
    sources: tuple[ResolvedChat, ...]


def _bot_entities(entities: list[Any] | None) -> list[dict[str, object]] | None:
    if not entities:
        return None
    entity_types: dict[type[Any], str] = {
        MessageEntityMention: "mention", MessageEntityHashtag: "hashtag",
        MessageEntityUrl: "url", MessageEntityEmail: "email",
        MessageEntityBold: "bold", MessageEntityItalic: "italic",
        MessageEntityCode: "code", MessageEntityPhone: "phone_number",
        MessageEntityUnderline: "underline", MessageEntityStrike: "strikethrough",
        MessageEntityBlockquote: "blockquote", MessageEntitySpoiler: "spoiler",
        MessageEntityCustomEmoji: "custom_emoji", MessageEntityTextUrl: "text_link",
        MessageEntityPre: "pre",
    }
    converted: list[dict[str, object]] = []
    for entity in entities:
        kind = entity_types.get(type(entity))
        if kind is None:
            continue
        item: dict[str, object] = {
            "type": kind, "offset": entity.offset, "length": entity.length,
        }
        if isinstance(entity, MessageEntityTextUrl):
            item["url"] = entity.url
        elif isinstance(entity, MessageEntityPre):
            item["language"] = entity.language
        elif isinstance(entity, MessageEntityCustomEmoji):
            item["custom_emoji_id"] = str(entity.document_id)
        converted.append(item)
    return converted or None


class TelegramGateway:
    """Own the Telethon account client and optional Bot API target transport."""

    def __init__(
        self,
        session_path: Path,
        api_id: int,
        api_hash: str,
        *,
        entity_cache_limit: int,
        bot_token: str | None = None,
        target_chat: ChatRef | None = None,
        phone: str | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        session_path.parent.mkdir(parents=True, exist_ok=True)
        client_factory = client_factory or TelegramClient
        self._client = client_factory(
            str(session_path), api_id, api_hash, auto_reconnect=True,
            connection_retries=None, retry_delay=2,
            entity_cache_limit=entity_cache_limit,
        )
        self._bot_gateway = (
            TelegramBotGateway(bot_token, target_chat) if bot_token else None
        )
        self._phone = phone
        self._user_id: int | None = None
        self._bot_entity: Any = None
        self._offset: int | None = None

    async def start_account(self, phone: str) -> tuple[str, int]:
        """Log into the user account and return its display name and id."""
        await self._client.start(phone=phone)
        account = await self._client.get_me()
        return utils.get_display_name(account), account.id

    async def prepare_monitoring(self, phone: str) -> tuple[str, int]:
        """Log in and initialize the private bot relay for live forwarding."""
        name, user_id = await self.start_account(phone)
        await self.start_relay(user_id)
        return name, user_id

    async def list_account_dialogs(
        self, phone: str
    ) -> tuple[tuple[str, int], tuple[Any, ...]]:
        """Log in once and return the account identity with all its dialogs."""
        identity = await self.start_account(phone)
        return identity, tuple(dialog async for dialog in self.dialogs())

    async def start(self) -> None:
        """Start the configured account for reset operations."""
        if self._phone is None:
            raise RuntimeError("Telefonnummer für den Telegram-Gateway fehlt.")
        await self.start_account(self._phone)
        await self.refresh_dialogs()

    async def login_resolve_chat(self, target_reference: ChatRef) -> int:
        """Log in, refresh account entities, and resolve a Bot API target chat."""
        await self.start()
        return await self.resolve_target(target_reference)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def close(self) -> None:
        """Release the account connection when a finite operation completes."""
        await self.disconnect()

    async def refresh_dialogs(self) -> None:
        await self._client.get_dialogs()

    async def resolve_entity(self, reference: ChatRef, *, refresh: bool = True) -> Any:
        try:
            return await self._client.get_entity(reference)
        except ValueError:
            if not refresh:
                raise
            await self.refresh_dialogs()
            return await self._client.get_entity(reference)

    async def resolve_entity_id(self, reference: ChatRef) -> tuple[Any, int]:
        entity = await self.resolve_entity(reference)
        return entity, utils.get_peer_id(entity)

    async def resolve_target_and_sources(
        self, target_reference: ChatRef, source_references: tuple[ChatRef, ...]
    ) -> ResolvedChats:
        """Resolve one Bot API target and every supplied account source together."""
        target = await self.resolve_bot_target(target_reference)
        sources: list[ResolvedChat] = []
        for reference in source_references:
            entity, source_id = await self.resolve_entity_id(reference)
            if source_id == target.id:
                raise ValueError("Quell- und Zielchat dürfen nicht identisch sein.")
            sources.append(
                ResolvedChat(
                    id=source_id,
                    entity=entity,
                    title=self.display_name(entity),
                    kind=self.source_kind(entity),
                )
            )
        return ResolvedChats(target, tuple(sources))

    async def message_boundary_before(
        self, reference: ChatRef, cutoff: datetime
    ) -> tuple[int, int]:
        entity, peer_id = await self.resolve_entity_id(reference)
        messages = await self._client.get_messages(entity, limit=1, offset_date=cutoff)
        return peer_id, messages[0].id if messages else 0

    async def delete_messages_in_batches(
        self, entity: Any, message_ids: tuple[int, ...], *, batch_size: int = 100
    ) -> None:
        for index in range(0, len(message_ids), batch_size):
            await self._client.delete_messages(
                entity, list(message_ids[index : index + batch_size]), revoke=True
            )

    async def dialogs(self) -> AsyncIterator[Any]:
        async for dialog in self._client.iter_dialogs():
            yield dialog

    async def get_messages(self, entity: Any, **kwargs: Any) -> Any:
        return await self._client.get_messages(entity, **kwargs)

    def iter_messages(self, entity: Any, **kwargs: Any) -> AsyncIterator[Any]:
        return self._client.iter_messages(entity, **kwargs)

    async def send_file(self, entity: Any, file: Any, **kwargs: Any) -> Any:
        return await self._client.send_file(entity, file, **kwargs)

    async def forward_messages(self, entity: Any, message: Any) -> Any:
        return await self._client.forward_messages(entity, message)

    async def delete_account_messages(
        self, entity: Any, message_ids: list[int], **kwargs: Any
    ) -> Any:
        return await self._client.delete_messages(entity, message_ids, **kwargs)

    def add_message_handler(
        self, callback: Callable[[Any], Any], chats: list[Any], *, edited: bool
    ) -> None:
        builder = events.MessageEdited(chats=chats) if edited else events.NewMessage(chats=chats)
        self._client.add_event_handler(callback, builder)

    def register_monitoring_handlers(
        self,
        on_new_message: Callable[[Any], Any],
        on_message_edited: Callable[[Any], Any],
        chats: list[Any],
    ) -> None:
        """Register both live-monitoring handlers for the same set of source chats."""
        self.add_message_handler(on_new_message, chats, edited=False)
        self.add_message_handler(on_message_edited, chats, edited=True)

    async def wait_until_disconnected(self) -> None:
        await self._client.run_until_disconnected()

    @staticmethod
    def display_name(entity: Any) -> str:
        return utils.get_display_name(entity)

    @staticmethod
    def peer_id(entity: Any) -> int:
        return utils.get_peer_id(entity)

    @staticmethod
    def source_kind(entity: Any) -> str:
        if isinstance(entity, Channel):
            return "supergroup" if entity.megagroup else "channel"
        if isinstance(entity, Chat):
            return "group"
        raise ValueError("Als Telegram-Quelle sind nur Gruppen und Kanäle erlaubt.")

    @staticmethod
    def make_text_link_entity(offset: int, length: int, url: str) -> Any:
        return MessageEntityTextUrl(offset=offset, length=length, url=url)

    @staticmethod
    def add_surrogate(text: str) -> str:
        return helpers.add_surrogate(text)

    @staticmethod
    def del_surrogate(text: str) -> str:
        return helpers.del_surrogate(text)

    async def start_relay(self, user_id: int) -> None:
        relay = await self._require_bot_gateway().start_relay()
        try:
            self._bot_entity = await self._client.get_input_entity(relay.identity.id)
        except ValueError:
            self._bot_entity = await self.resolve_entity(f"@{relay.identity.username}")
        self._user_id = user_id
        self._offset = relay.update_offset

    async def resolve_bot_target(self, reference: ChatRef) -> BotChat:
        return await self._require_bot_gateway().resolve_chat(reference)

    async def resolve_target(self, reference: ChatRef) -> int:
        return (await self.resolve_bot_target(reference)).id

    async def resolve_source(self, reference: ChatRef) -> int:
        _, source_id = await self.resolve_entity_id(reference)
        return source_id

    async def delete_target_messages(self, message_ids: tuple[int, ...]) -> None:
        await self._require_bot_gateway().delete_target_messages(message_ids)

    async def send_message(
        self, entity: BotChat, text: str, *, parse_mode: object = None
    ) -> SimpleNamespace:
        del entity, parse_mode
        return SimpleNamespace(
            id=await self._require_bot_gateway().send_target_message(text)
        )

    async def edit_message(
        self, entity: BotChat, message_id: int, text: str, *, parse_mode: object = None
    ) -> None:
        del entity, parse_mode
        await self._require_bot_gateway().edit_target_message(message_id, text)

    async def edit_caption(
        self, entity: BotChat, message_id: int, caption: str, entities: list[Any] | None
    ) -> None:
        del entity
        await self._require_bot_gateway().edit_target_caption(
            message_id, caption, _bot_entities(entities)
        )

    async def copy_message(
        self, source_id: int, source_message_id: int, message: Any,
        *, caption: str | None, entities: list[Any] | None,
    ) -> SimpleNamespace:
        del source_id, source_message_id
        if self._user_id is None or self._bot_entity is None:
            raise RuntimeError("Bot-Relay wurde nicht gestartet.")

        marker = f"telegram-voice-forwarder:{secrets.token_urlsafe(18)}"
        relay_message: Any = None
        incoming_message_id: int | None = None
        try:
            media = message.voice or message.video_note
            relay_message = await self.send_file(
                self._bot_entity, media, caption=marker, parse_mode=None, silent=True,
                voice_note=bool(message.voice), video_note=bool(message.video_note),
            )
            incoming_message_id = await self._wait_for_relay(marker)
            copied_id = await self._require_bot_gateway().copy_relay_message(
                self._user_id,
                incoming_message_id,
                caption=caption,
                entities=_bot_entities(entities),
                is_voice=bool(message.voice),
            )
            return SimpleNamespace(id=copied_id)
        finally:
            if incoming_message_id is not None:
                try:
                    await self._require_bot_gateway().delete_private_message(
                        self._user_id, incoming_message_id
                    )
                except TelegramBotApiError:
                    LOGGER.warning("Bot-Relay-Nachricht konnte nicht gelöscht werden")
            elif relay_message is not None:
                try:
                    await self.delete_account_messages(
                        self._bot_entity, [relay_message.id], revoke=True
                    )
                except Exception:
                    LOGGER.warning(
                        "Nicht zugestellte Bot-Relay-Nachricht konnte nicht gelöscht werden",
                        exc_info=True,
                    )

    async def delete_messages(
        self, entity: BotChat, message_ids: list[int], *, revoke: bool = True
    ) -> None:
        del entity, revoke
        await self.delete_target_messages(tuple(message_ids))

    async def _wait_for_relay(self, marker: str) -> int:
        if self._user_id is None:
            raise RuntimeError("Bot-Relay wurde nicht gestartet.")
        deadline = time.monotonic() + RELAY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            updates = await self._require_bot_gateway().poll_message_updates(
                self._offset, min(10, max(1, int(deadline - time.monotonic())))
            )
            for update in updates or []:
                self._offset = int(update["update_id"]) + 1
                message = update.get("message")
                if not isinstance(message, dict):
                    continue
                chat = message.get("chat")
                sender = message.get("from")
                if (
                    isinstance(chat, dict) and chat.get("id") == self._user_id
                    and isinstance(sender, dict) and sender.get("id") == self._user_id
                    and message.get("caption") == marker
                ):
                    return self._message_id(message)
        raise TelegramBotApiError("Bot-Relay-Nachricht wurde nicht rechtzeitig empfangen.")

    def _require_bot_gateway(self) -> TelegramBotGateway:
        if self._bot_gateway is None:
            raise RuntimeError("Dieser Telegram-Gateway hat keinen Bot konfiguriert.")
        return self._bot_gateway

    @staticmethod
    def _message_id(message: object) -> int:
        if not isinstance(message, dict) or not isinstance(message.get("message_id"), int):
            raise TelegramBotApiError("Telegram lieferte keine Ziel-Nachrichten-ID.")
        return message["message_id"]
