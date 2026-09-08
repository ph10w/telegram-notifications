from typing import Protocol

from tg_api.bot_gateway import TelegramBotGateway


class NotificationSink(Protocol):
    async def send_text(self, text: str) -> None: ...


class TelegramNotificationSink:
    """Send arbitrary text notifications to the configured general target chat."""

    def __init__(self, gateway: TelegramBotGateway) -> None:
        self._gateway = gateway

    async def send_text(self, text: str) -> None:
        await self._gateway.send_text(text)

    async def send(self, title: str, body: str) -> None:
        await self.send_text(f"{title}\n\n{body}")
