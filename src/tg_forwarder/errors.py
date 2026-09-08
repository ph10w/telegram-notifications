class TelegramServiceError(RuntimeError):
    """A Telegram adapter operation failed."""


from tg_api.errors import TelegramApiError

TelegramBotApiError = TelegramApiError
