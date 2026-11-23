import asyncio
import json
import math
import os
import subprocess
import sys
import tempfile
from contextlib import suppress
from datetime import datetime
from typing import Any, Callable, Mapping

from .errors import GeminiUsageError

DEFAULT_GEMINI_USAGE_ARGS: tuple[str, ...] = (
    "--settings",
    '{"modelProvider":""}',
    "-p",
    "/usage",
    "--output-format",
    "json",
    "--print-timeout",
    "15s",
)
DEFAULT_GEMINI_USAGE_COMMAND: tuple[str, ...] = ("agy", *DEFAULT_GEMINI_USAGE_ARGS)
DEFAULT_GEMINI_USAGE_GROUP = "Gemini Models"
GEMINI_USAGE_TIMEOUT_SECONDS = 30.0
GEMINI_SHUTDOWN_TIMEOUT_SECONDS = 5.0

_WINDOW_SPECS: dict[str, tuple[str, int]] = {
    "5h": ("primary", 300),
    "weekly": ("secondary", 10_080),
}
_STRIPPED_API_KEY_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _subprocess_options() -> dict[str, Any]:
    if sys.platform != "win32":
        return {}
    options: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startupinfo
    return options


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def _remaining_fraction_to_used_percent(remaining_fraction: float) -> float:
    if remaining_fraction >= 1.0:
        return 0.0
    if remaining_fraction <= 0.0:
        return 100.0
    return min(99.9, max(0.1, round((1.0 - remaining_fraction) * 100.0, 1)))


def _parse_reset_time(value: object) -> int | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return int(datetime.fromisoformat(value.strip()).timestamp())
    except ValueError:
        return None


def _find_group(groups: list[object], group_name: str) -> dict[str, Any] | None:
    normalized_target = group_name.strip().lower()
    fallback: dict[str, Any] | None = None
    for entry in groups:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        if name == group_name:
            return entry
        if fallback is None and name.strip().lower() == normalized_target:
            fallback = entry
    return fallback


def normalize_gemini_usage_payload(
    raw_payload: object,
    *,
    group_name: str = DEFAULT_GEMINI_USAGE_GROUP,
    limit_id: str = "gemini",
) -> dict[str, Any]:
    """Convert ``agy -p /usage --output-format json`` output to rate-limit buckets."""
    if not isinstance(raw_payload, dict):
        return {}
    if raw_payload.get("status") == "ERROR":
        detail = raw_payload.get("error") or "Unbekannter Antigravity-CLI-Fehler"
        raise GeminiUsageError(str(detail))

    command = raw_payload.get("command")
    if not isinstance(command, dict):
        return {}
    data = command.get("data")
    if not isinstance(data, dict):
        return {}
    groups = data.get("groups")
    if not isinstance(groups, list):
        return {}

    group = _find_group(groups, group_name)
    if group is None:
        return {}
    buckets = group.get("buckets")
    if not isinstance(buckets, list):
        return {}

    windows: dict[str, dict[str, Any]] = {}
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        spec = _WINDOW_SPECS.get(str(bucket.get("window", "")).strip().lower())
        if spec is None:
            continue
        remaining = _number(bucket.get("remaining_fraction"))
        if remaining is None or not 0.0 <= remaining <= 1.0:
            continue
        slot_name, duration_minutes = spec
        window_payload: dict[str, Any] = {
            "usedPercent": _remaining_fraction_to_used_percent(remaining),
            "windowDurationMins": duration_minutes,
        }
        resets_at = _parse_reset_time(bucket.get("reset_time"))
        if resets_at is not None:
            window_payload["resetsAt"] = resets_at
        windows[slot_name] = window_payload

    if not windows:
        return {}
    return {"rateLimits": {"limitId": limit_id, **windows}}


def _parse_json_output(stdout_text: str) -> dict[str, Any] | None:
    stripped = stdout_text.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    for line in reversed(stripped.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _clean_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = dict(os.environ)
    for key in _STRIPPED_API_KEY_VARS:
        environment.pop(key, None)
    if overrides is not None:
        environment.update(overrides)
    return environment


class GeminiUsageClient:
    """Runs the local Antigravity CLI ``/usage`` command and parses its JSON output."""

    def __init__(
        self,
        command: tuple[str, ...] = DEFAULT_GEMINI_USAGE_COMMAND,
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = GEMINI_USAGE_TIMEOUT_SECONDS,
    ) -> None:
        self._command = command
        self._environment = _clean_environment(environment)
        self._timeout_seconds = timeout_seconds

    async def read_usage(self) -> dict[str, Any]:
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tempfile.gettempdir(),
                env=self._environment,
                **_subprocess_options(),
            )
        except OSError as exc:
            raise GeminiUsageError(
                f"Antigravity-CLI konnte nicht gestartet werden: {exc}"
            ) from exc

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except TimeoutError as exc:
            await self._terminate_process(process)
            raise GeminiUsageError(
                "Zeitüberschreitung bei der Gemini-Nutzungsabfrage über die "
                "Antigravity-CLI."
            ) from exc

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
        payload = _parse_json_output(stdout_text)

        if process.returncode != 0:
            if isinstance(payload, dict) and payload.get("error"):
                raise GeminiUsageError(str(payload["error"]))
            detail = stderr_text or stdout_text.strip() or f"Exit-Code {process.returncode}"
            raise GeminiUsageError(
                f"Gemini-Nutzungsabfrage fehlgeschlagen: {detail}"
            )

        if payload is None:
            raise GeminiUsageError(
                "Antigravity-CLI lieferte kein gültiges JSON für /usage."
            )
        if payload.get("status") == "ERROR":
            detail = payload.get("error") or "Unbekannter Antigravity-CLI-Fehler"
            raise GeminiUsageError(str(detail))
        return payload

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if sys.platform == "win32":
            with suppress(OSError, subprocess.SubprocessError):
                await asyncio.to_thread(
                    subprocess.run,
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    timeout=GEMINI_SHUTDOWN_TIMEOUT_SECONDS,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_subprocess_options(),
                )
        else:
            with suppress(ProcessLookupError):
                process.kill()
        with suppress(TimeoutError, OSError):
            await asyncio.wait_for(
                process.communicate(), timeout=GEMINI_SHUTDOWN_TIMEOUT_SECONDS
            )


class GeminiUsageRateLimitReader:
    """Reads normalized Gemini rate-limit windows from the local Antigravity CLI."""

    def __init__(
        self,
        command: tuple[str, ...] = DEFAULT_GEMINI_USAGE_COMMAND,
        *,
        group_name: str = DEFAULT_GEMINI_USAGE_GROUP,
        limit_id: str = "gemini",
        client_factory: Callable[[tuple[str, ...]], GeminiUsageClient] | None = None,
    ) -> None:
        self._command = command
        self._group_name = group_name
        self._limit_id = limit_id
        self._client_factory = client_factory or self._create_client

    async def read_rate_limits(self) -> dict[str, Any]:
        client = self._client_factory(self._command)
        raw_payload = await client.read_usage()
        return normalize_gemini_usage_payload(
            raw_payload,
            group_name=self._group_name,
            limit_id=self._limit_id,
        )

    @staticmethod
    def _create_client(command: tuple[str, ...]) -> GeminiUsageClient:
        return GeminiUsageClient(command)
