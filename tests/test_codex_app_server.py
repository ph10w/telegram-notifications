import unittest

from telegram_notifications.codex_app_server import (
    CodexAppServerRateLimitReader,
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
            client_factory=lambda _: client,  # type: ignore[arg-type]
        )

        self.assertEqual(await reader.read_rate_limits(), {"rateLimits": {}})
        self.assertTrue(client.started)
        self.assertTrue(client.closed)


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


if __name__ == "__main__":
    unittest.main()
