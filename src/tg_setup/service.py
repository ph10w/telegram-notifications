import logging

from tg_api.telegram_gateway import RPCError, TelegramGateway

from .config import AccountConfig
from .errors import SetupError, TelegramBotApiError
from .models import DialogInfo

LOGGER = logging.getLogger(__name__)


def _load_dialogs(dialogs: tuple[object, ...]) -> tuple[DialogInfo, ...]:
    result: list[DialogInfo] = []
    for dialog in dialogs:
        if dialog.is_group:
            kind = "Gruppe"
        elif dialog.is_channel:
            kind = "Kanal"
        elif dialog.is_user:
            kind = "Benutzer"
        else:
            kind = "Sonstiges"
        result.append(DialogInfo(dialog.id, kind, dialog.name))
    return tuple(result)


async def list_available_chats(config: AccountConfig) -> tuple[DialogInfo, ...]:
    client = TelegramGateway(
        config.session_path,
        config.api_id,
        config.api_hash,
        entity_cache_limit=config.entity_cache_limit,
        phone=config.phone,
    )
    try:
        (name, user_id), dialogs = await client.list_account_dialogs(config.phone)
        LOGGER.info("Angemeldet als %s (ID %s)", name, user_id)
        return _load_dialogs(dialogs)
    except (RPCError, TelegramBotApiError) as exc:
        raise SetupError(str(exc)) from exc
    finally:
        await client.disconnect()
