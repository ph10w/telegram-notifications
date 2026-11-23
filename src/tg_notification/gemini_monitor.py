import logging
from pathlib import Path

from .codex_app_server import RateLimitReader
from .codex_monitor import CodexRateLimitMonitor, WEEKLY_WINDOW_MINUTES
from .errors import GeminiUsageError
from .gemini_usage import DEFAULT_GEMINI_USAGE_GROUP
from .sinks import NotificationSink

LOGGER = logging.getLogger(__name__)


class GeminiRateLimitMonitor(CodexRateLimitMonitor):
    """Monitor 5-hour and weekly Gemini model quotas using the local Antigravity CLI."""

    def __init__(
        self,
        reader: RateLimitReader,
        sink: NotificationSink,
        *,
        state_path: Path,
        group_name: str = DEFAULT_GEMINI_USAGE_GROUP,
        limit_id: str = "gemini",
        window_minutes: int = 300,
        poll_seconds: float = 900.0,
        additional_window_minutes: tuple[int, ...] = (WEEKLY_WINDOW_MINUTES,),
    ) -> None:
        super().__init__(
            reader,
            sink,
            state_path=state_path,
            rate_limit_id=limit_id,
            window_minutes=window_minutes,
            poll_seconds=poll_seconds,
            additional_window_minutes=additional_window_minutes,
            service_name=group_name,
            notification_title="Gemini-Nutzung",
            log_label="Gemini",
        )

    async def check_once(self, *, report_limits: bool = False) -> bool:
        try:
            return await super().check_once(report_limits=report_limits)
        except GeminiUsageError as exc:
            LOGGER.warning("Gemini-Nutzungsabfrage fehlgeschlagen: %s", exc)
            return False
