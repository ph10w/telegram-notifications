import logging
from datetime import timedelta

from tg_api.telegram_gateway import RPCError, TelegramGateway

from .app import VoiceForwarder
from .config import ChatRef, ForwarderConfig
from .errors import TelegramBotApiError, TelegramServiceError
from .reset_service import ResetResult, reset_scan_state
from .state import StateStore

LOGGER = logging.getLogger(__name__)


async def run_monitoring(config: ForwarderConfig) -> None:
    client = TelegramGateway(
        config.session_path,
        config.api_id,
        config.api_hash,
        entity_cache_limit=config.entity_cache_limit,
        bot_token=config.notification_bot_token,
        target_chat=config.target_chat,
        phone=config.phone,
    )
    state = StateStore(config.state_db)
    try:
        name, user_id = await client.prepare_monitoring(config.phone)
        LOGGER.info("Angemeldet als %s (ID %s)", name, user_id)
        await VoiceForwarder(client, config, state, relay_via_bot=True).run()
    except (RPCError, TelegramBotApiError) as exc:
        raise TelegramServiceError(str(exc)) from exc
    finally:
        state.close()
        await client.disconnect()


async def reset_forwarder(
    config: ForwarderConfig,
    period: timedelta | None,
    source_chat: ChatRef | None = None,
) -> ResetResult:
    gateway = TelegramGateway(
        config.session_path,
        config.api_id,
        config.api_hash,
        entity_cache_limit=config.entity_cache_limit,
        bot_token=config.notification_bot_token,
        target_chat=config.target_chat,
        phone=config.phone,
    )
    state = StateStore(config.state_db)
    try:
        return await reset_scan_state(
            config,
            period,
            source_chat=source_chat,
            telegram=gateway,
            state=state,
        )
    except (RPCError, TelegramBotApiError) as exc:
        raise TelegramServiceError(str(exc)) from exc
