import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .errors import TelegramApiError

REQUEST_TIMEOUT_SECONDS = 40


@dataclass(frozen=True, slots=True)
class BotIdentity:
    id: int
    username: str


class _TelegramBotTransport:
    """Private synchronous transport for the Telegram Bot API."""

    def __init__(self, token: str) -> None:
        self._base_url = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, **parameters: object) -> Any:
        encoded = urlencode(
            {
                key: json.dumps(value) if isinstance(value, (list, dict)) else value
                for key, value in parameters.items()
                if value is not None
            }
        ).encode("utf-8")
        request = Request(
            self._base_url + method,
            data=encoded,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                payload = json.load(response)
        except HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
                description = payload.get("description", "Telegram API error")
            except (UnicodeDecodeError, json.JSONDecodeError):
                description = f"Telegram API returned HTTP {exc.code}"
            raise TelegramApiError(str(description)) from None
        except URLError as exc:
            raise TelegramApiError(
                f"Telegram API is unavailable: {exc.reason}"
            ) from None
        except json.JSONDecodeError:
            raise TelegramApiError("Telegram API returned invalid JSON") from None

        if not isinstance(payload, dict) or not payload.get("ok"):
            description = (
                payload.get("description", "Telegram API error")
                if isinstance(payload, dict)
                else "Telegram API returned an invalid response"
            )
            raise TelegramApiError(str(description))
        return payload.get("result")

    def identity(self) -> BotIdentity:
        result = self.call("getMe")
        username = result.get("username") if isinstance(result, dict) else None
        bot_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(username, str) or not username:
            raise TelegramApiError("Telegram returned no username for this bot.")
        if not isinstance(bot_id, int):
            raise TelegramApiError("Telegram returned no ID for this bot.")
        return BotIdentity(bot_id, username)

    def send_text(self, chat_id: int | str, text: str) -> None:
        self.call(
            "sendMessage", chat_id=chat_id, text=text, disable_notification=False
        )

    def latest_update_offset(self) -> int | None:
        updates = self.call("getUpdates", timeout=0, limit=100) or []
        if not updates:
            return None
        return max(int(update["update_id"]) for update in updates) + 1

    def wait_for_private_start(
        self,
        start_parameter: str,
        *,
        offset: int | None,
        timeout_seconds: int,
        poll_timeout_seconds: int,
    ) -> int:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            updates = self.call(
                "getUpdates", offset=offset, timeout=poll_timeout_seconds,
                allowed_updates=["message"],
            ) or []
            for update in updates:
                offset = int(update["update_id"]) + 1
                message = update.get("message")
                chat = message.get("chat") if isinstance(message, dict) else None
                if (
                    isinstance(chat, dict)
                    and chat.get("type") == "private"
                    and str(message.get("text", "")) == f"/start {start_parameter}"
                    and isinstance(chat.get("id"), int)
                ):
                    return chat["id"]
        raise TelegramApiError(
            "No matching private /start message was received within the setup timeout."
        )

    def ensure_can_publish(self, chat_id: int | str, bot_id: int) -> None:
        target = self.call("getChat", chat_id=chat_id)
        membership = self.call("getChatMember", chat_id=chat_id, user_id=bot_id)
        status = membership.get("status") if isinstance(membership, dict) else None
        if status not in {"administrator", "creator"}:
            raise TelegramApiError(
                "Add the bot to TELEGRAM_TARGET_CHAT as an administrator and rerun setup."
            )
        if (
            isinstance(target, dict)
            and target.get("type") == "channel"
            and not membership.get("can_post_messages")
        ):
            raise TelegramApiError(
                "Grant the bot permission to post messages in TELEGRAM_TARGET_CHAT."
            )
