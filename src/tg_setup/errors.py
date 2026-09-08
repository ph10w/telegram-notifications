from tg_api.errors import TelegramApiError


class SetupError(RuntimeError):
    """An interactive Telegram setup operation failed."""


TelegramBotApiError = TelegramApiError
