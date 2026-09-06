import asyncio
from typing import Protocol

from .config import ChatRef
from .telegram_api import TelegramBotApi


class NotificationSink(Protocol):
    async def send_text(self, text: str) -> None: ...


class TelegramNotificationSink:
    """Send arbitrary text notifications to the configured general target chat."""

    def __init__(self, api: TelegramBotApi, target_chat: ChatRef) -> None:
        self._api = api
        self._target_chat = target_chat

    async def send_text(self, text: str) -> None:
        await asyncio.to_thread(
            self._api.call,
            "sendMessage",
            chat_id=self._target_chat,
            text=text,
            disable_notification=False,
        )

    async def send(self, title: str, body: str) -> None:
        await self.send_text(f"{title}\n\n{body}")
