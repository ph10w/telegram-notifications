import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_notification.errors import GeminiUsageError
from tg_notification.gemini_monitor import GeminiRateLimitMonitor


class RecordingSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(text)


class StaticReader:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = iter(payloads)

    async def read_rate_limits(self) -> dict[str, object]:
        return next(self._payloads)


class FailingReader:
    async def read_rate_limits(self) -> dict[str, object]:
        raise GeminiUsageError("agy unavailable")


def _gemini_payload(
    *,
    five_hour_used: float,
    five_hour_resets_at: int,
    weekly_used: float,
    weekly_resets_at: int,
) -> dict[str, object]:
    return {
        "rateLimits": {
            "limitId": "gemini",
            "primary": {
                "usedPercent": five_hour_used,
                "windowDurationMins": 300,
                "resetsAt": five_hour_resets_at,
            },
            "secondary": {
                "usedPercent": weekly_used,
                "windowDurationMins": 10_080,
                "resetsAt": weekly_resets_at,
            },
        }
    }


class GeminiRateLimitMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_sends_gemini_branded_pre_reset_and_reset_notifications(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = GeminiRateLimitMonitor(
                reader=StaticReader([]),
                sink=sink,
                state_path=Path(directory) / "gemini-state.json",
            )
            five_hour_reset = 1_800_000_000
            weekly_reset = 1_800_500_000

            await monitor.observe(
                _gemini_payload(
                    five_hour_used=40.0,
                    five_hour_resets_at=five_hour_reset,
                    weekly_used=15.0,
                    weekly_resets_at=weekly_reset,
                )
            )

            with patch("tg_notification.codex_monitor.time.time", return_value=five_hour_reset - 300):
                self.assertTrue(await monitor._send_due_scheduled_notifications())

            with patch("tg_notification.codex_monitor.time.time", return_value=five_hour_reset + 5):
                self.assertTrue(await monitor._send_due_scheduled_notifications())

            with patch("tg_notification.codex_monitor.time.time", return_value=weekly_reset - 600):
                self.assertTrue(await monitor._send_due_scheduled_notifications())

            self.assertEqual(len(sink.messages), 3)
            self.assertTrue(sink.messages[0].startswith("Gemini-Nutzung\n\n"))
            self.assertIn("5-Stunden-Nutzungsfenster von Gemini Models", sink.messages[0])
            self.assertIn("in 5 Minuten", sink.messages[0])
            self.assertIn("5-Stunden-Nutzungsfenster von Gemini Models wird jetzt", sink.messages[1])
            self.assertIn("Wochen-Nutzungsfenster von Gemini Models", sink.messages[2])
            self.assertIn("in 10 Minuten", sink.messages[2])

    async def test_suppresses_five_hour_notification_while_weekly_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = GeminiRateLimitMonitor(
                reader=StaticReader([]),
                sink=sink,
                state_path=Path(directory) / "gemini-state.json",
            )
            five_hour_reset = 1_800_000_000
            weekly_reset = 1_800_500_000

            await monitor.observe(
                _gemini_payload(
                    five_hour_used=100.0,
                    five_hour_resets_at=five_hour_reset,
                    weekly_used=100.0,
                    weekly_resets_at=weekly_reset,
                )
            )

            with patch("tg_notification.codex_monitor.time.time", return_value=five_hour_reset + 5):
                self.assertFalse(await monitor._send_due_scheduled_notifications())
            self.assertEqual(sink.messages, [])

    async def test_check_once_logs_warning_and_returns_false_on_cli_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = GeminiRateLimitMonitor(
                reader=FailingReader(),
                sink=sink,
                state_path=Path(directory) / "gemini-state.json",
            )

            with self.assertLogs("tg_notification.gemini_monitor", level="WARNING") as logs:
                result = await monitor.check_once(report_limits=True)

            self.assertFalse(result)
            self.assertEqual(sink.messages, [])
            self.assertTrue(any("agy unavailable" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()
