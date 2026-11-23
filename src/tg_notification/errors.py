class NotificationConfigError(ValueError):
    """Notification configuration is missing or invalid."""


class TelegramNotificationError(RuntimeError):
    """A Telegram Bot API operation failed."""


class CodexAppServerError(RuntimeError):
    """The Codex app-server could not be started or queried."""


class GeminiUsageError(RuntimeError):
    """The local Antigravity/Gemini CLI could not be queried for usage."""
