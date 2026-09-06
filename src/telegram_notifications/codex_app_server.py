import asyncio
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .errors import CodexAppServerError


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    limit_id: str
    used_percent: float
    window_duration_minutes: int | None
    resets_at: int | None


@dataclass(frozen=True, slots=True)
class RateLimitWindow:
    name: str
    used_percent: float
    window_duration_minutes: int | None
    resets_at: int | None


class RateLimitReader(Protocol):
    async def read_rate_limits(self) -> dict[str, Any]: ...


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def extract_rate_limit_snapshot(
    payload: object,
    *,
    limit_id: str = "codex",
    window_minutes: int = 300,
) -> RateLimitSnapshot | None:
    if not isinstance(payload, dict):
        return None

    buckets = payload.get("rateLimitsByLimitId")
    if isinstance(buckets, dict):
        bucket = buckets.get(limit_id)
    else:
        bucket = payload.get("rateLimits")
        if isinstance(bucket, dict):
            returned_limit_id = bucket.get("limitId")
            if returned_limit_id is not None and returned_limit_id != limit_id:
                return None
    if not isinstance(bucket, dict):
        return None

    primary = bucket.get("primary")
    if not isinstance(primary, dict):
        return None
    used_percent = _number(primary.get("usedPercent"))
    if used_percent is None or not 0 <= used_percent <= 100:
        return None

    duration_value = _number(primary.get("windowDurationMins"))
    duration = int(duration_value) if duration_value is not None else None
    if duration is not None and duration != window_minutes:
        return None

    reset_value = _number(primary.get("resetsAt"))
    resets_at = int(reset_value) if reset_value is not None else None
    return RateLimitSnapshot(
        limit_id=limit_id,
        used_percent=used_percent,
        window_duration_minutes=duration,
        resets_at=resets_at,
    )


def extract_rate_limit_windows(
    payload: object, *, limit_id: str = "codex"
) -> tuple[RateLimitWindow, ...]:
    if not isinstance(payload, dict):
        return ()

    buckets = payload.get("rateLimitsByLimitId")
    if isinstance(buckets, dict):
        bucket = buckets.get(limit_id)
    else:
        bucket = payload.get("rateLimits")
        if isinstance(bucket, dict) and bucket.get("limitId") not in (None, limit_id):
            return ()
    if not isinstance(bucket, dict):
        return ()

    windows: list[RateLimitWindow] = []
    for name in ("primary", "secondary"):
        window = bucket.get(name)
        if not isinstance(window, dict):
            continue
        used_percent = _number(window.get("usedPercent"))
        if used_percent is None or not 0 <= used_percent <= 100:
            continue
        duration_value = _number(window.get("windowDurationMins"))
        duration = int(duration_value) if duration_value is not None else None
        reset_value = _number(window.get("resetsAt"))
        windows.append(
            RateLimitWindow(
                name=name,
                used_percent=used_percent,
                window_duration_minutes=duration,
                resets_at=int(reset_value) if reset_value is not None else None,
            )
        )
    return tuple(windows)


class CodexAppServerClient:
    """Minimal JSONL client for the local ``codex app-server`` process."""

    def __init__(self, command: tuple[str, ...]) -> None:
        self._command = command
        self._process: asyncio.subprocess.Process | None = None
        self._next_request_id = 1

    async def start(self) -> None:
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise CodexAppServerError(
                f"Codex-App-Server konnte nicht gestartet werden: {exc}"
            ) from exc

        try:
            await self._request(
                "initialize",
                {
                    "clientInfo": {
                        "name": "telegram_notifications",
                        "title": "Telegram Notifications",
                        "version": "0.1.0",
                    }
                },
            )
            await self._send({"method": "initialized", "params": {}})
        except Exception:
            await self.close()
            raise

    async def read_rate_limits(self) -> dict[str, Any]:
        result = await self._request("account/rateLimits/read", {})
        if not isinstance(result, dict):
            raise CodexAppServerError(
                "Codex-App-Server lieferte keine gültigen Rate-Limit-Daten."
            )
        return result

    async def wait_for_rate_limit_update(self, timeout: float) -> dict[str, Any] | None:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            message = await asyncio.wait_for(self._read_message(), remaining)
            if message.get("method") != "account/rateLimits/updated":
                continue
            params = message.get("params")
            return params if isinstance(params, dict) else None

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

    async def _request(self, method: str, params: dict[str, object]) -> object:
        request_id = self._next_request_id
        self._next_request_id += 1
        await self._send({"method": method, "id": request_id, "params": params})
        while True:
            message = await self._read_message()
            if message.get("id") != request_id:
                continue
            error = message.get("error")
            if isinstance(error, dict):
                description = error.get("message", "Unbekannter App-Server-Fehler")
                raise CodexAppServerError(str(description))
            return message.get("result")

    async def _send(self, message: dict[str, object]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex-App-Server ist nicht gestartet.")
        process.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode())
        await process.stdin.drain()

    async def _read_message(self) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdout is None:
            raise CodexAppServerError("Codex-App-Server ist nicht gestartet.")
        while True:
            line = await process.stdout.readline()
            if not line:
                raise CodexAppServerError("Codex-App-Server wurde beendet.")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexAppServerError(
                    "Codex-App-Server lieferte ungültiges JSON."
                ) from exc
            if isinstance(message, dict):
                return message


class CodexAppServerRateLimitReader:
    """Reads rate limits from a freshly started local app-server process."""

    def __init__(
        self,
        command: tuple[str, ...],
        *,
        client_factory: Callable[[tuple[str, ...]], CodexAppServerClient] = (
            CodexAppServerClient
        ),
    ) -> None:
        self._command = command
        self._client_factory = client_factory

    async def read_rate_limits(self) -> dict[str, Any]:
        client = self._client_factory(self._command)
        await client.start()
        try:
            return await client.read_rate_limits()
        finally:
            await client.close()
