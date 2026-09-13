import unittest
from unittest.mock import patch

from pathlib import Path

from tg_notification.codex_accounts import CodexAccountProfile
from tg_notification.codex_app_server import (
    CodexAccountRateLimitReader,
    CodexAppServerRateLimitReader,
    _app_server_subprocess_options,
    extract_rate_limit_snapshot,
    extract_rate_limit_windows,
)


class FakeAppServerClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.started = False
        self.closed = False

    async def start(self) -> None:
        self.started = True

    async def read_rate_limits(self) -> dict[str, object]:
        return self._payload

    async def close(self) -> None:
        self.closed = True


class CodexAppServerRateLimitReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_and_closes_a_fresh_app_server_client(self) -> None:
        client = FakeAppServerClient({"rateLimits": {}})
        reader = CodexAppServerRateLimitReader(
            ("codex", "app-server"),
            client_factory=lambda _, __: client,  # type: ignore[arg-type]
        )

        self.assertEqual(await reader.read_rate_limits(), {"rateLimits": {}})
        self.assertTrue(client.started)
        self.assertTrue(client.closed)

    async def test_reads_only_the_active_profile_with_its_own_codex_home(self) -> None:
        active = CodexAccountProfile("account-a", Path("profiles/account-a"))
        environments: list[dict[str, str]] = []

        class ProfileStore:
            def active_profile(self) -> CodexAccountProfile | None:
                return active

        def reader_factory(
            _: tuple[str, ...], environment: dict[str, str]
        ) -> FakeAppServerClient:
            environments.append(environment)
            return FakeAppServerClient({"rateLimits": {}})

        reader = CodexAccountRateLimitReader(
            ("codex", "app-server"),
            ProfileStore(),  # type: ignore[arg-type]
            reader_factory=reader_factory,
        )

        results = await reader.read_active_rate_limits()

        self.assertEqual([result.account_id for result in results], ["account-a"])
        self.assertEqual(environments, [{"CODEX_HOME": "profiles\\account-a"}])

    async def test_returns_no_results_without_an_active_profile(self) -> None:
        class ProfileStore:
            def active_profile(self) -> CodexAccountProfile | None:
                return None

        reader = CodexAccountRateLimitReader(
            ("codex", "app-server"),
            ProfileStore(),  # type: ignore[arg-type]
            reader_factory=lambda _, __: FakeAppServerClient({"rateLimits": {}}),
        )

        self.assertEqual(await reader.read_active_rate_limits(), ())


class ExtractRateLimitWindowsTests(unittest.TestCase):
    def test_returns_primary_and_secondary_windows(self) -> None:
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

        windows = extract_rate_limit_windows(payload)

        self.assertEqual([window.name for window in windows], ["primary", "secondary"])
        self.assertEqual([window.window_duration_minutes for window in windows], [300, 10_080])

    def test_extracts_the_weekly_secondary_window(self) -> None:
        payload = {
            "rateLimits": {
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

        weekly = extract_rate_limit_snapshot(payload, window_minutes=10_080)

        self.assertIsNotNone(weekly)
        assert weekly is not None
        self.assertEqual(weekly.used_percent, 50)
        self.assertEqual(weekly.resets_at, 1_800_604_800)


class AppServerSubprocessOptionsTests(unittest.TestCase):
    def test_hides_the_app_server_console_on_windows(self) -> None:
        with patch("tg_notification.codex_app_server.sys.platform", "win32"):
            self.assertIn("creationflags", _app_server_subprocess_options())

    def test_omits_windows_options_on_other_platforms(self) -> None:
        with patch("tg_notification.codex_app_server.sys.platform", "linux"):
            self.assertEqual(_app_server_subprocess_options(), {})


if __name__ == "__main__":
    unittest.main()
