import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from telegram_notifications.codex_monitor import CodexRateLimitMonitor


class RecordingSink:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_text(self, text: str) -> None:
        self.messages.append(text)


class RecordingReader:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = iter(payloads)
        self.read_count = 0

    async def read_rate_limits(self) -> dict[str, object]:
        self.read_count += 1
        return next(self._payloads)


def _rate_limits(*, used_percent: float, resets_at: int) -> dict[str, object]:
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": used_percent,
                "windowDurationMins": 300,
                "resetsAt": resets_at,
            }
        }
    }


class CodexRateLimitMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_notifies_once_when_a_full_window_resets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )

            self.assertFalse(
                await monitor.observe(_rate_limits(used_percent=100, resets_at=1_800_000_000))
            )
            self.assertTrue(
                await monitor.observe(_rate_limits(used_percent=0, resets_at=1_800_018_000))
            )
            self.assertFalse(
                await monitor.observe(_rate_limits(used_percent=0, resets_at=1_800_018_000))
            )

            self.assertEqual(len(sink.messages), 1)
            self.assertIn("wurde zurückgesetzt", sink.messages[0])

    async def test_saved_snapshot_suppresses_duplicate_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            first_sink = RecordingSink()
            first_monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=first_sink,
                state_path=state_path,
            )
            await first_monitor.observe(
                _rate_limits(used_percent=100, resets_at=1_800_000_000)
            )
            await first_monitor.observe(
                _rate_limits(used_percent=0, resets_at=1_800_018_000)
            )

            second_sink = RecordingSink()
            second_monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=second_sink,
                state_path=state_path,
            )
            self.assertFalse(
                await second_monitor.observe(
                    _rate_limits(used_percent=0, resets_at=1_800_018_000)
                )
            )
            self.assertEqual(second_sink.messages, [])

    async def test_sends_pre_reset_and_predicted_reset_notifications_once(self) -> None:
        reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(_rate_limits(used_percent=100, resets_at=reset_at))

            with patch("telegram_notifications.codex_monitor.time.time", return_value=reset_at - 300):
                self.assertTrue(await monitor._send_due_scheduled_notifications())
                self.assertFalse(await monitor._send_due_scheduled_notifications())
            with patch("telegram_notifications.codex_monitor.time.time", return_value=reset_at):
                self.assertTrue(await monitor._send_due_scheduled_notifications())
                self.assertFalse(await monitor._send_due_scheduled_notifications())

            self.assertEqual(len(sink.messages), 2)
            self.assertIn("in 5 Minuten", sink.messages[0])
            self.assertIn("wird jetzt", sink.messages[1])

    async def test_predicted_reset_prevents_duplicate_after_following_poll(self) -> None:
        reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(_rate_limits(used_percent=100, resets_at=reset_at))
            with patch("telegram_notifications.codex_monitor.time.time", return_value=reset_at):
                await monitor._send_due_scheduled_notifications()

            self.assertFalse(
                await monitor.observe(
                    _rate_limits(used_percent=0, resets_at=reset_at + 18_000)
                )
            )
            self.assertEqual(len(sink.messages), 1)

    async def test_schedules_one_confirmation_poll_after_predicted_reset(self) -> None:
        reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=RecordingSink(),
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(_rate_limits(used_percent=100, resets_at=reset_at))
            with patch("telegram_notifications.codex_monitor.time.time", return_value=reset_at):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=reset_at + 15,
            ):
                self.assertTrue(monitor._reset_confirmation_poll_due())
                self.assertEqual(monitor._seconds_until_confirmation_poll(), 0.0)

    async def test_confirmation_poll_does_not_suppress_the_next_reset_poll(self) -> None:
        first_reset_at = 2_000_000_000
        second_reset_at = first_reset_at + 18_000
        with tempfile.TemporaryDirectory() as directory:
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=RecordingSink(),
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(
                _rate_limits(used_percent=100, resets_at=first_reset_at)
            )
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=first_reset_at,
            ):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=first_reset_at + 15,
            ):
                monitor._mark_reset_confirmation_polled()
            await monitor.observe(
                _rate_limits(used_percent=50, resets_at=second_reset_at)
            )
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=second_reset_at,
            ):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=second_reset_at + 15,
            ):
                self.assertTrue(monitor._reset_confirmation_poll_due())

    async def test_unused_window_does_not_schedule_reset_work(self) -> None:
        reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(_rate_limits(used_percent=0, resets_at=reset_at))

            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=reset_at - 300,
            ):
                self.assertIsNone(monitor._seconds_until_scheduled_notification())
                self.assertFalse(await monitor._send_due_scheduled_notifications())
            with patch(
                "telegram_notifications.codex_monitor.time.time",
                return_value=reset_at + 15,
            ):
                self.assertIsNone(monitor._seconds_until_confirmation_poll())
                self.assertFalse(monitor._reset_confirmation_poll_due())

            self.assertEqual(sink.messages, [])

    async def test_check_once_reads_one_fresh_snapshot(self) -> None:
        payload = _rate_limits(used_percent=75, resets_at=1_800_000_000)
        reader = RecordingReader([payload])
        with tempfile.TemporaryDirectory() as directory:
            monitor = CodexRateLimitMonitor(
                reader=reader,
                sink=RecordingSink(),
                state_path=Path(directory) / "state.json",
            )

            self.assertFalse(await monitor.check_once())
            self.assertEqual(reader.read_count, 1)

    async def test_first_check_reports_primary_and_weekly_windows(self) -> None:
        payload = {
            "rateLimits": {
                "limitId": "codex",
                "primary": {
                    "usedPercent": 25,
                    "windowDurationMins": 300,
                    "resetsAt": 1_800_000_000,
                },
                "secondary": {
                    "usedPercent": 50,
                    "windowDurationMins": 10_080,
                    "resetsAt": 1_800_604_800,
                },
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([payload]),
                sink=RecordingSink(),
                state_path=Path(directory) / "state.json",
            )
            with patch("telegram_notifications.codex_monitor.LOGGER.info") as info:
                await monitor.check_once(report_limits=True)

        report = info.call_args.args[1]
        self.assertIn("5-Stunden-Fenster", report)
        self.assertIn("Wochenfenster", report)


if __name__ == "__main__":
    unittest.main()
