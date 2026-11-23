import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tg_notification.errors import GeminiUsageError
from tg_notification.gemini_usage import (
    GeminiUsageClient,
    GeminiUsageRateLimitReader,
    _subprocess_options,
    normalize_gemini_usage_payload,
)


def _sample_agy_usage_payload(
    *,
    gemini_5h_remaining: float = 0.8054,
    gemini_5h_reset: str = "2026-11-24T02:31:34Z",
    gemini_weekly_remaining: float = 0.9509,
    gemini_weekly_reset: str = "2026-11-30T21:31:34Z",
) -> dict[str, object]:
    return {
        "conversation_id": "",
        "status": "SUCCESS",
        "command": {
            "name": "usage",
            "data": {
                "groups": [
                    {
                        "name": "Gemini Models",
                        "buckets": [
                            {
                                "id": "gemini-weekly",
                                "name": "Weekly Limit Remaining",
                                "window": "weekly",
                                "remaining_fraction": gemini_weekly_remaining,
                                "reset_time": gemini_weekly_reset,
                            },
                            {
                                "id": "gemini-5h",
                                "name": "Five Hour Limit Remaining",
                                "window": "5h",
                                "remaining_fraction": gemini_5h_remaining,
                                "reset_time": gemini_5h_reset,
                            },
                        ],
                    },
                    {
                        "name": "Claude and GPT models",
                        "buckets": [
                            {
                                "id": "3p-weekly",
                                "window": "weekly",
                                "remaining_fraction": 0.1,
                                "reset_time": "2026-11-30T22:36:10Z",
                            },
                            {
                                "id": "3p-5h",
                                "window": "5h",
                                "remaining_fraction": 0.2,
                                "reset_time": "2026-11-24T03:36:10Z",
                            },
                        ],
                    },
                ]
            },
        },
    }


class GeminiUsageNormalizationTests(unittest.TestCase):
    def test_normalizes_five_hour_and_weekly_gemini_buckets(self) -> None:
        normalized = normalize_gemini_usage_payload(_sample_agy_usage_payload())

        expected_5h = int(
            datetime(2026, 11, 24, 2, 31, 34, tzinfo=timezone.utc).timestamp()
        )
        expected_weekly = int(
            datetime(2026, 11, 30, 21, 31, 34, tzinfo=timezone.utc).timestamp()
        )
        self.assertEqual(
            normalized,
            {
                "rateLimits": {
                    "limitId": "gemini",
                    "primary": {
                        "usedPercent": 19.5,
                        "windowDurationMins": 300,
                        "resetsAt": expected_5h,
                    },
                    "secondary": {
                        "usedPercent": 4.9,
                        "windowDurationMins": 10_080,
                        "resetsAt": expected_weekly,
                    },
                }
            },
        )

    def test_preserves_exact_zero_and_hundred_percent_boundaries(self) -> None:
        unused = normalize_gemini_usage_payload(
            _sample_agy_usage_payload(
                gemini_5h_remaining=1.0, gemini_weekly_remaining=0.0
            )
        )
        self.assertEqual(unused["rateLimits"]["primary"]["usedPercent"], 0.0)
        self.assertEqual(unused["rateLimits"]["secondary"]["usedPercent"], 100.0)

        barely_used = normalize_gemini_usage_payload(
            _sample_agy_usage_payload(
                gemini_5h_remaining=0.9999, gemini_weekly_remaining=0.0001
            )
        )
        self.assertEqual(barely_used["rateLimits"]["primary"]["usedPercent"], 0.1)
        self.assertEqual(barely_used["rateLimits"]["secondary"]["usedPercent"], 99.9)

    def test_raises_on_error_status(self) -> None:
        with self.assertRaisesRegex(GeminiUsageError, "not available"):
            normalize_gemini_usage_payload(
                {"status": "ERROR", "error": "/usage failed: not available"}
            )

    def test_returns_empty_dict_when_group_is_missing(self) -> None:
        self.assertEqual(
            normalize_gemini_usage_payload(
                _sample_agy_usage_payload(), group_name="Other Models"
            ),
            {},
        )


class GeminiUsageClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_strips_gemini_api_key_env_vars_and_parses_json(self) -> None:
        fake_process = MagicMock()
        fake_process.returncode = 0
        fake_process.communicate = AsyncMock(
            return_value=(b'{"status":"SUCCESS","command":{"name":"usage"}}', b"")
        )
        captured_env: dict[str, str] = {}
        captured_kwargs: dict[str, object] = {}

        async def fake_exec(*args: object, **kwargs: object) -> MagicMock:
            captured_env.update(kwargs["env"])  # type: ignore[arg-type]
            captured_kwargs.update(kwargs)
            return fake_process

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "secret-1",
                "GOOGLE_API_KEY": "secret-2",
                "KEEP_ME": "yes",
            },
            clear=True,
        ):
            client = GeminiUsageClient(("agy", "-p", "/usage"))
            with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
                payload = await client.read_usage()

        self.assertEqual(payload["status"], "SUCCESS")
        self.assertNotIn("GEMINI_API_KEY", captured_env)
        self.assertNotIn("GOOGLE_API_KEY", captured_env)
        self.assertEqual(captured_env.get("KEEP_ME"), "yes")
        self.assertEqual(captured_kwargs.get("cwd"), tempfile.gettempdir())
        if sys.platform == "win32":
            options = _subprocess_options()
            self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
            self.assertEqual(options["startupinfo"].wShowWindow, subprocess.SW_HIDE)

    async def test_raises_gemini_usage_error_on_nonzero_exit(self) -> None:
        fake_process = MagicMock()
        fake_process.returncode = 1
        fake_process.communicate = AsyncMock(
            return_value=(
                b'error: failed\n{"status":"ERROR","error":"auth expired"}\n',
                b"",
            )
        )

        client = GeminiUsageClient(("agy", "-p", "/usage"))
        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=fake_process)):
            with self.assertRaisesRegex(GeminiUsageError, "auth expired"):
                await client.read_usage()

    async def test_reader_normalizes_client_output(self) -> None:
        fake_client = MagicMock()
        fake_client.read_usage = AsyncMock(return_value=_sample_agy_usage_payload())
        reader = GeminiUsageRateLimitReader(
            ("agy",),
            client_factory=lambda _cmd: fake_client,
        )

        result = await reader.read_rate_limits()

        self.assertEqual(result["rateLimits"]["limitId"], "gemini")
        self.assertEqual(result["rateLimits"]["primary"]["windowDurationMins"], 300)
        self.assertEqual(result["rateLimits"]["secondary"]["windowDurationMins"], 10_080)


if __name__ == "__main__":
    unittest.main()
