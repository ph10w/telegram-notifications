import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_notification.codex_monitor import CodexRateLimitMonitor
from tg_notification.codex_app_server import AccountRateLimitResult
from tg_notification.codex_monitor import MultiAccountCodexRateLimitMonitor


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


class RecordingAccountReader:
    def __init__(self, reads: list[tuple[AccountRateLimitResult, ...]]) -> None:
        self._reads = iter(reads)

    async def read_all_rate_limits(self) -> tuple[AccountRateLimitResult, ...]:
        return next(self._reads)


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


def _primary_and_weekly_rate_limits(
    *,
    primary_used_percent: float,
    primary_resets_at: int,
    weekly_used_percent: float,
    weekly_resets_at: int,
) -> dict[str, object]:
    return {
        "rateLimits": {
            "primary": {
                "usedPercent": primary_used_percent,
                "windowDurationMins": 300,
                "resetsAt": primary_resets_at,
            },
            "secondary": {
                "usedPercent": weekly_used_percent,
                "windowDurationMins": 10_080,
                "resetsAt": weekly_resets_at,
            },
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

            with patch("tg_notification.codex_monitor.time.time", return_value=reset_at - 300):
                self.assertTrue(await monitor._send_due_scheduled_notifications())
                self.assertFalse(await monitor._send_due_scheduled_notifications())
            with patch("tg_notification.codex_monitor.time.time", return_value=reset_at):
                self.assertTrue(await monitor._send_due_scheduled_notifications())
                self.assertFalse(await monitor._send_due_scheduled_notifications())

            self.assertEqual(len(sink.messages), 2)
            self.assertIn("in 5 Minuten", sink.messages[0])
            self.assertIn("wird jetzt", sink.messages[1])

    async def test_sends_weekly_pre_reset_and_predicted_reset_notifications_once(self) -> None:
        primary_reset_at = 2_000_018_000
        weekly_reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )
            await monitor.observe(
                _primary_and_weekly_rate_limits(
                    primary_used_percent=0,
                    primary_resets_at=primary_reset_at,
                    weekly_used_percent=25,
                    weekly_resets_at=weekly_reset_at,
                )
            )

            with patch("tg_notification.codex_monitor.time.time", return_value=weekly_reset_at - 300):
                self.assertTrue(await monitor._send_due_scheduled_notifications())
            with patch("tg_notification.codex_monitor.time.time", return_value=weekly_reset_at):
                self.assertTrue(await monitor._send_due_scheduled_notifications())

            self.assertEqual(len(sink.messages), 2)
            self.assertIn("Wochen-Nutzungsfenster", sink.messages[0])
            self.assertIn("in 5 Minuten", sink.messages[0])
            self.assertIn("Wochen-Nutzungsfenster", sink.messages[1])
            self.assertIn("wird jetzt", sink.messages[1])

    async def test_stores_both_windows_in_one_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([]),
                sink=RecordingSink(),
                state_path=state_path,
            )

            await monitor.observe(
                _primary_and_weekly_rate_limits(
                    primary_used_percent=25,
                    primary_resets_at=2_000_018_000,
                    weekly_used_percent=50,
                    weekly_resets_at=2_000_000_000,
                )
            )

            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload["windows"]), {"five_hour", "weekly"})

    async def test_keeps_rate_limit_state_and_notifications_per_account(self) -> None:
        first_reset = 2_000_000_000
        second_reset = first_reset + 18_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            monitor = MultiAccountCodexRateLimitMonitor(
                RecordingAccountReader(
                    [
                        (
                            AccountRateLimitResult(
                                "account-alpha-12345",
                                _rate_limits(used_percent=100, resets_at=first_reset),
                            ),
                            AccountRateLimitResult(
                                "account-beta-67890",
                                _rate_limits(used_percent=100, resets_at=first_reset),
                            ),
                        ),
                        (
                            AccountRateLimitResult(
                                "account-alpha-12345",
                                _rate_limits(used_percent=0, resets_at=second_reset),
                            ),
                            AccountRateLimitResult(
                                "account-beta-67890",
                                _rate_limits(used_percent=0, resets_at=second_reset),
                            ),
                        ),
                    ]
                ),
                sink,
                state_path=Path(directory) / "state.json",
            )

            self.assertFalse(await monitor.check_once())
            self.assertTrue(await monitor.check_once())

            self.assertEqual(len(sink.messages), 2)
            self.assertIn("Konto account-…", sink.messages[0])
            self.assertIn("Konto account-…", sink.messages[1])
            payload = json.loads((Path(directory) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                set(payload["accounts"]),
                {"account-alpha-12345", "account-beta-67890"},
            )

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
            with patch("tg_notification.codex_monitor.time.time", return_value=reset_at):
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
            with patch("tg_notification.codex_monitor.time.time", return_value=reset_at):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "tg_notification.codex_monitor.time.time",
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
                "tg_notification.codex_monitor.time.time",
                return_value=first_reset_at,
            ):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "tg_notification.codex_monitor.time.time",
                return_value=first_reset_at + 15,
            ):
                monitor._mark_reset_confirmation_polled()
            await monitor.observe(
                _rate_limits(used_percent=50, resets_at=second_reset_at)
            )
            with patch(
                "tg_notification.codex_monitor.time.time",
                return_value=second_reset_at,
            ):
                await monitor._send_due_scheduled_notifications()
            with patch(
                "tg_notification.codex_monitor.time.time",
                return_value=second_reset_at + 15,
            ):
                self.assertTrue(monitor._reset_confirmation_poll_due())

    async def test_unused_window_does_not_schedule_reset_work(self) -> None:
        reset_at = 2_000_000_000
        with tempfile.TemporaryDirectory() as directory:
            sink = RecordingSink()
            unused_window = _rate_limits(used_percent=0, resets_at=reset_at)
            monitor = CodexRateLimitMonitor(
                reader=RecordingReader([unused_window, unused_window]),
                sink=sink,
                state_path=Path(directory) / "state.json",
            )
            await monitor.check_once(report_limits=True)
            await monitor.check_once()

            with patch(
                "tg_notification.codex_monitor.time.time",
                return_value=reset_at - 300,
            ):
                self.assertIsNone(monitor._seconds_until_scheduled_notification())
                self.assertFalse(await monitor._send_due_scheduled_notifications())
            with patch(
                "tg_notification.codex_monitor.time.time",
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
            with patch("tg_notification.codex_monitor.LOGGER.info") as info:
                await monitor.check_once(report_limits=True)

        report = info.call_args.args[1]
        self.assertIn("5-Stunden-Fenster", report)
        self.assertIn("Wochenfenster", report)


if __name__ == "__main__":
    unittest.main()
