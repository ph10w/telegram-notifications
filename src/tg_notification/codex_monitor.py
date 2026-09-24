import asyncio
import json
import logging
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .codex_accounts import CodexAccountProfileStore
from .codex_app_server import (
    AccountRateLimitReader,
    RateLimitSnapshot,
    RateLimitReader,
    RateLimitWindow,
    extract_rate_limit_snapshot,
    extract_rate_limit_windows,
)
from .sinks import NotificationSink

LOGGER = logging.getLogger(__name__)
FIVE_HOUR_PRE_RESET_SECONDS = 600
WEEKLY_PRE_RESET_SECONDS = 1_200
DEFAULT_PRE_RESET_SECONDS = 300
FIVE_HOUR_MAX_OVERDUE_RESET_SECONDS = 3_600
WEEKLY_MAX_OVERDUE_RESET_SECONDS = 28_800
RESET_CONFIRMATION_DELAY_SECONDS = 15
WEEKLY_WINDOW_MINUTES = 10_080
ACCOUNT_ID_DISPLAY_LENGTH = 8


class RateLimitStateStore:
    def __init__(
        self, path: Path, *, window_key: str, account_id: str | None = None
    ) -> None:
        self._path = path
        self._window_key = window_key
        self._account_id = account_id

    def load(self) -> tuple[RateLimitSnapshot | None, int | None, int | None, int | None]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None, None, None, None
        if not isinstance(payload, dict):
            return None, None, None, None
        if self._account_id is not None:
            accounts = payload.get("accounts")
            if not isinstance(accounts, dict):
                return None, None, None, None
            payload = accounts.get(self._account_id)
            if not isinstance(payload, dict):
                return None, None, None, None
        windows = payload.get("windows")
        if not isinstance(windows, dict):
            return None, None, None, None
        payload = windows.get(self._window_key)
        if not isinstance(payload, dict):
            return None, None, None, None
        try:
            snapshot = RateLimitSnapshot(
                limit_id=str(payload["limit_id"]),
                used_percent=float(payload["used_percent"]),
                window_duration_minutes=int(payload["window_duration_minutes"]),
                resets_at=(
                    _parse_state_timestamp(payload["resets_at"])
                    if payload.get("resets_at") is not None
                    else None
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None, None, None, None
        reset_notified = payload.get(
            "last_reset_notified_at", payload.get("last_notified_resets_at")
        )
        pre_reset_notified = payload.get("last_pre_reset_notified_at")
        confirmation_polled = payload.get("last_reset_confirmation_polled_at")
        return (
            snapshot,
            _parse_state_timestamp(reset_notified),
            _parse_state_timestamp(pre_reset_notified),
            _parse_state_timestamp(confirmation_polled),
        )

    def save(
        self,
        snapshot: RateLimitSnapshot,
        *,
        last_reset_notified_at: int | None,
        last_pre_reset_notified_at: int | None,
        last_reset_confirmation_polled_at: int | None,
        last_polled_at: int | None = None,
        account_email: str | None = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        window_payload: dict[str, Any] = {
            **asdict(snapshot),
            "resets_at": _format_state_timestamp(snapshot.resets_at),
            "last_reset_notified_at": _format_state_timestamp(
                last_reset_notified_at
            ),
            "last_pre_reset_notified_at": _format_state_timestamp(
                last_pre_reset_notified_at
            ),
            "last_reset_confirmation_polled_at": _format_state_timestamp(
                last_reset_confirmation_polled_at
            ),
        }
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if self._account_id is None:
            windows = payload.get("windows")
            if not isinstance(windows, dict):
                windows = {}
            windows[self._window_key] = window_payload
            payload["windows"] = windows
        else:
            accounts = payload.get("accounts")
            if not isinstance(accounts, dict):
                accounts = {}
            account_payload = accounts.get(self._account_id)
            if not isinstance(account_payload, dict):
                account_payload = {}
            if last_polled_at is not None:
                account_payload["last_polled_at"] = _format_state_timestamp(
                    last_polled_at
                )
            if account_email is not None:
                account_payload["email"] = account_email
            windows = account_payload.get("windows")
            if not isinstance(windows, dict):
                windows = {}
            windows[self._window_key] = window_payload
            account_payload["windows"] = windows
            accounts[self._account_id] = account_payload
            payload["accounts"] = accounts
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self._path)

    def account_email(self) -> str | None:
        if self._account_id is None:
            return None
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            email = payload["accounts"][self._account_id].get("email")
        except (FileNotFoundError, OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
        return email.strip() if isinstance(email, str) and email.strip() else None


def _format_state_timestamp(value: int | None) -> int | str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return value


def _parse_state_timestamp(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(datetime.fromisoformat(value).timestamp())
        except ValueError:
            return None
    return None


def _reset_detected(
    previous: RateLimitSnapshot | None, current: RateLimitSnapshot
) -> bool:
    if previous is None:
        return False
    if previous.used_percent < 100 or current.used_percent >= previous.used_percent:
        return False
    if (
        previous.resets_at is not None
        and current.resets_at is not None
        and previous.resets_at == current.resets_at
    ):
        return False
    return True


def _window_description(snapshot: RateLimitSnapshot) -> str:
    if snapshot.window_duration_minutes == 300:
        return "5-Stunden-Nutzungsfenster"
    if snapshot.window_duration_minutes == WEEKLY_WINDOW_MINUTES:
        return "Wochen-Nutzungsfenster"
    return "Codex-Nutzungsfenster"


def _reset_message(snapshot: RateLimitSnapshot) -> str:
    available_percent = max(0.0, 100.0 - snapshot.used_percent)
    message = (
        f"Das {_window_description(snapshot)} von Codex/Work wurde zurückgesetzt.\n"
        f"Aktuelle Nutzung: {snapshot.used_percent:g} % "
        f"({available_percent:g} % verfügbar)."
    )
    if snapshot.resets_at is not None:
        try:
            next_reset = datetime.fromtimestamp(snapshot.resets_at).astimezone()
        except (OSError, OverflowError, ValueError):
            LOGGER.warning("Ungültiger Codex-Resetzeitpunkt: %s", snapshot.resets_at)
        else:
            message += f"\nNächster Reset: {next_reset:%Y-%m-%d %H:%M:%S %Z}"
    return message


def _pre_reset_message(snapshot: RateLimitSnapshot) -> str:
    minutes = _pre_reset_seconds(snapshot) // 60
    return (
        f"Das {_window_description(snapshot)} von Codex/Work wird voraussichtlich "
        f"in {minutes} Minuten zurückgesetzt.\n"
        f"Aktuelle Nutzung: {snapshot.used_percent:g} %."
    )


def _scheduled_reset_message(snapshot: RateLimitSnapshot) -> str:
    return (
        f"Das {_window_description(snapshot)} von Codex/Work wird jetzt gemäß dem "
        "zuletzt gelesenen Resetzeitpunkt zurückgesetzt. Der nächste Poll "
        "bestätigt den aktuellen Nutzungsstand."
    )


def _reset_relevant(snapshot: RateLimitSnapshot) -> bool:
    """Only schedule reset work after the current window has been used."""
    return snapshot.used_percent > 0


def _reset_notification_is_timely(snapshot: RateLimitSnapshot, now: float) -> bool:
    max_overdue_seconds = (
        WEEKLY_MAX_OVERDUE_RESET_SECONDS
        if snapshot.window_duration_minutes == WEEKLY_WINDOW_MINUTES
        else FIVE_HOUR_MAX_OVERDUE_RESET_SECONDS
    )
    return (
        snapshot.resets_at is None
        or now <= snapshot.resets_at + max_overdue_seconds
    )


def _pre_reset_seconds(snapshot: RateLimitSnapshot) -> int:
    if snapshot.window_duration_minutes == 300:
        return FIVE_HOUR_PRE_RESET_SECONDS
    if snapshot.window_duration_minutes == WEEKLY_WINDOW_MINUTES:
        return WEEKLY_PRE_RESET_SECONDS
    return DEFAULT_PRE_RESET_SECONDS


def _window_name(window: RateLimitWindow) -> str:
    if window.window_duration_minutes == 300:
        return "5-Stunden-Fenster"
    if window.window_duration_minutes == 10_080:
        return "Wochenfenster"
    if window.name == "primary":
        return "Primäres Nutzungsfenster"
    return "Sekundäres Nutzungsfenster"


def _window_state_key(window_minutes: int) -> str:
    if window_minutes == 300:
        return "five_hour"
    if window_minutes == WEEKLY_WINDOW_MINUTES:
        return "weekly"
    return f"{window_minutes}_minutes"


def _window_status(window: RateLimitWindow) -> str:
    available_percent = max(0.0, 100.0 - window.used_percent)
    status = (
        f"{_window_name(window)}: {window.used_percent:g} % genutzt, "
        f"{available_percent:g} % verfügbar"
    )
    if window.resets_at is None:
        return status
    try:
        reset_at = datetime.fromtimestamp(window.resets_at).astimezone()
    except (OSError, OverflowError, ValueError):
        return status
    return f"{status}, Reset: {reset_at:%Y-%m-%d %H:%M:%S %Z}"


def _display_account_id(account_id: str) -> str:
    if len(account_id) <= ACCOUNT_ID_DISPLAY_LENGTH:
        return account_id
    return f"{account_id[:ACCOUNT_ID_DISPLAY_LENGTH]}…"


class CodexRateLimitMonitor:
    def __init__(
        self,
        reader: RateLimitReader,
        sink: NotificationSink,
        *,
        state_path: Path,
        rate_limit_id: str = "codex",
        window_minutes: int = 300,
        poll_seconds: float = 60.0,
        additional_window_minutes: tuple[int, ...] = (WEEKLY_WINDOW_MINUTES,),
        account_id: str | None = None,
        account_email: str | None = None,
    ) -> None:
        self._reader = reader
        self._sink = sink
        self._state = RateLimitStateStore(
            state_path,
            window_key=_window_state_key(window_minutes),
            account_id=account_id,
        )
        self._account_id = account_id
        self._account_email = account_email or self._state.account_email()
        self._rate_limit_id = rate_limit_id
        self._window_minutes = window_minutes
        self._poll_seconds = poll_seconds
        self._additional_monitors = tuple(
            CodexRateLimitMonitor(
                reader,
                sink,
                state_path=state_path,
                rate_limit_id=rate_limit_id,
                window_minutes=additional_window,
                poll_seconds=poll_seconds,
                additional_window_minutes=(),
                account_id=account_id,
                account_email=self._account_email,
            )
            for additional_window in additional_window_minutes
            if additional_window != window_minutes
        )

    async def run(self) -> None:
        LOGGER.info(
            "Codex-Nutzungsmonitor gestartet; Abfrage alle %g Sekunden.",
            self._poll_seconds,
        )
        await self.check_once(report_limits=True)
        next_poll_at = asyncio.get_running_loop().time() + self._poll_seconds
        while True:
            delay_until_poll = max(0.0, next_poll_at - asyncio.get_running_loop().time())
            delay_until_notification = self._seconds_until_any_scheduled_notification()
            delay_until_confirmation = self._seconds_until_any_confirmation_poll()
            delay = min(
                delay_until_poll,
                delay_until_notification
                if delay_until_notification is not None
                else delay_until_poll,
                delay_until_confirmation
                if delay_until_confirmation is not None
                else delay_until_poll,
            )
            await asyncio.sleep(delay)
            await self._send_due_scheduled_notifications()
            if self._reset_confirmation_poll_due():
                self._mark_reset_confirmation_polled()
                await self.check_once()
                next_poll_at = (
                    asyncio.get_running_loop().time() + self._poll_seconds
                )
            elif asyncio.get_running_loop().time() >= next_poll_at:
                await self.check_once()
                next_poll_at = (
                    asyncio.get_running_loop().time() + self._poll_seconds
                )

    async def check_once(self, *, report_limits: bool = False) -> bool:
        payload = await self._reader.read_rate_limits()
        if report_limits:
            windows = extract_rate_limit_windows(payload, limit_id=self._rate_limit_id)
            if windows:
                LOGGER.info(
                    "Codex-Nutzungsstand beim Start:\n%s",
                    "\n".join(_window_status(window) for window in windows),
                )
            else:
                LOGGER.warning(
                    "Der App-Server lieferte beim Start keine lesbaren Codex-Nutzungsfenster."
                )
        return await self.observe(payload)

    async def observe(self, payload: object) -> bool:
        notified_now = await self._observe_window(payload)
        for monitor in self._additional_monitors:
            notified_now = await monitor.observe(payload) or notified_now
        return notified_now

    async def _observe_window(self, payload: object) -> bool:
        snapshot = extract_rate_limit_snapshot(
            payload,
            limit_id=self._rate_limit_id,
            window_minutes=self._window_minutes,
        )
        if snapshot is None:
            LOGGER.debug("Kein passendes Codex-5-Stunden-Rate-Limit gefunden.")
            return False

        (
            previous,
            last_reset_notified,
            last_pre_reset_notified,
            last_reset_confirmation_polled,
        ) = self._state.load()
        notified_now = False
        if (
            _reset_detected(previous, snapshot)
            and previous is not None
            and previous.resets_at != last_reset_notified
            and _reset_notification_is_timely(previous, time.time())
        ):
            await self._sink.send_text(
                self._notification_prefix() + _reset_message(snapshot)
            )
            last_reset_notified = previous.resets_at
            notified_now = True
        self._state.save(
            snapshot,
            last_reset_notified_at=last_reset_notified,
            last_pre_reset_notified_at=last_pre_reset_notified,
            last_reset_confirmation_polled_at=last_reset_confirmation_polled,
            last_polled_at=(
                int(time.time()) if self._account_id is not None else None
            ),
            account_email=self._account_email,
        )
        return notified_now

    def _seconds_until_any_scheduled_notification(self) -> float | None:
        delays = [self._seconds_until_scheduled_notification()]
        delays.extend(
            monitor._seconds_until_scheduled_notification()
            for monitor in self._additional_monitors
        )
        available_delays = [delay for delay in delays if delay is not None]
        return min(available_delays) if available_delays else None

    def _seconds_until_any_confirmation_poll(self) -> float | None:
        delays = [self._seconds_until_confirmation_poll()]
        delays.extend(
            monitor._seconds_until_confirmation_poll()
            for monitor in self._additional_monitors
        )
        available_delays = [delay for delay in delays if delay is not None]
        return min(available_delays) if available_delays else None

    def _seconds_until_scheduled_notification(self) -> float | None:
        snapshot, last_reset_notified, last_pre_reset_notified, _ = self._state.load()
        if (
            snapshot is None
            or snapshot.resets_at is None
            or not _reset_relevant(snapshot)
        ):
            return None
        now = time.time()
        if not _reset_notification_is_timely(snapshot, now):
            return None
        pre_reset_at = snapshot.resets_at - _pre_reset_seconds(snapshot)
        if (
            last_pre_reset_notified != snapshot.resets_at
            and pre_reset_at <= now < snapshot.resets_at
        ):
            return 0.0
        if last_reset_notified != snapshot.resets_at and now >= snapshot.resets_at:
            return 0.0
        candidates: list[float] = []
        if (
            last_pre_reset_notified != snapshot.resets_at
            and pre_reset_at > now
        ):
            candidates.append(pre_reset_at - now)
        if last_reset_notified != snapshot.resets_at and snapshot.resets_at > now:
            candidates.append(snapshot.resets_at - now)
        if candidates:
            return min(candidates)
        if (
            last_reset_notified != snapshot.resets_at
            or (
                last_pre_reset_notified != snapshot.resets_at
                and now < snapshot.resets_at
            )
        ):
            return 0.0
        return None

    async def _send_due_scheduled_notifications(self) -> bool:
        sent = await self._send_due_window_notifications()
        for monitor in self._additional_monitors:
            sent = await monitor._send_due_window_notifications() or sent
        return sent

    async def _send_due_window_notifications(self) -> bool:
        (
            snapshot,
            last_reset_notified,
            last_pre_reset_notified,
            last_reset_confirmation_polled,
        ) = self._state.load()
        if (
            snapshot is None
            or snapshot.resets_at is None
            or not _reset_relevant(snapshot)
        ):
            return False
        now = time.time()
        if not _reset_notification_is_timely(snapshot, now):
            return False
        if (
            last_pre_reset_notified != snapshot.resets_at
            and snapshot.resets_at - _pre_reset_seconds(snapshot)
            <= now
            < snapshot.resets_at
        ):
            await self._sink.send_text(
                self._notification_prefix() + _pre_reset_message(snapshot)
            )
            self._state.save(
                snapshot,
                last_reset_notified_at=last_reset_notified,
                last_pre_reset_notified_at=snapshot.resets_at,
                last_reset_confirmation_polled_at=last_reset_confirmation_polled,
            )
            return True
        if last_reset_notified != snapshot.resets_at and now >= snapshot.resets_at:
            await self._sink.send_text(
                self._notification_prefix() + _scheduled_reset_message(snapshot)
            )
            self._state.save(
                snapshot,
                last_reset_notified_at=snapshot.resets_at,
                last_pre_reset_notified_at=last_pre_reset_notified,
                last_reset_confirmation_polled_at=last_reset_confirmation_polled,
            )
            return True
        return False

    def _seconds_until_confirmation_poll(self) -> float | None:
        snapshot, last_reset_notified, _, last_confirmation_polled = self._state.load()
        if (
            snapshot is None
            or snapshot.resets_at is None
            or not _reset_relevant(snapshot)
            or last_reset_notified != snapshot.resets_at
            or last_confirmation_polled == snapshot.resets_at
        ):
            return None
        return max(0.0, snapshot.resets_at + RESET_CONFIRMATION_DELAY_SECONDS - time.time())

    def _reset_confirmation_poll_due(self) -> bool:
        return any(
            monitor._seconds_until_confirmation_poll() == 0.0
            for monitor in (self, *self._additional_monitors)
        )

    def _mark_reset_confirmation_polled(self) -> None:
        for monitor in (self, *self._additional_monitors):
            if monitor._seconds_until_confirmation_poll() == 0.0:
                monitor._mark_window_reset_confirmation_polled()

    def _mark_window_reset_confirmation_polled(self) -> None:
        (
            snapshot,
            last_reset_notified,
            last_pre_reset_notified,
            _,
        ) = self._state.load()
        if snapshot is None or snapshot.resets_at is None:
            return
        self._state.save(
            snapshot,
            last_reset_notified_at=last_reset_notified,
            last_pre_reset_notified_at=last_pre_reset_notified,
            last_reset_confirmation_polled_at=snapshot.resets_at,
        )

    def _notification_prefix(self) -> str:
        if self._account_id is None:
            return "Codex-Nutzung\n\n"
        if self._account_email is not None:
            return f"Codex-Nutzung – Konto {self._account_email}\n\n"
        return f"Codex-Nutzung – Konto {_display_account_id(self._account_id)}\n\n"

    def set_account_email(self, account_email: str | None) -> None:
        if account_email is None:
            return
        self._account_email = account_email
        for monitor in self._additional_monitors:
            monitor.set_account_email(account_email)


class MultiAccountCodexRateLimitMonitor:
    """Poll the signed-in Codex account while keeping every account state isolated.

    Only the currently signed-in account is polled. Notification timers of the
    other saved accounts keep running from their stored reset timestamps.
    """

    def __init__(
        self,
        reader: AccountRateLimitReader,
        sink: NotificationSink,
        *,
        state_path: Path,
        rate_limit_id: str = "codex",
        poll_seconds: float = 60.0,
        profile_store: CodexAccountProfileStore | None = None,
    ) -> None:
        self._reader = reader
        self._sink = sink
        self._state_path = state_path
        self._rate_limit_id = rate_limit_id
        self._poll_seconds = poll_seconds
        self._profile_store = profile_store
        self._monitors: dict[str, CodexRateLimitMonitor] = {}

    def _ensure_saved_account_monitors(self) -> None:
        if self._profile_store is None:
            return
        for profile in self._profile_store.profiles():
            self._monitor_for(profile.account_id)

    async def run(self) -> None:
        LOGGER.info(
            "Mehrkonto-Codex-Nutzungsmonitor gestartet; Abfrage alle %g Sekunden.",
            self._poll_seconds,
        )
        self._ensure_saved_account_monitors()
        await self.check_once(report_limits=True)
        next_poll_at = asyncio.get_running_loop().time() + self._poll_seconds
        while True:
            delay_until_poll = max(0.0, next_poll_at - asyncio.get_running_loop().time())
            notification_delays = [
                monitor._seconds_until_any_scheduled_notification()
                for monitor in self._monitors.values()
            ]
            confirmation_delays = [
                monitor._seconds_until_any_confirmation_poll()
                for monitor in self._monitors.values()
            ]
            available_delays = [delay_until_poll]
            available_delays.extend(
                delay for delay in notification_delays if delay is not None
            )
            available_delays.extend(
                delay for delay in confirmation_delays if delay is not None
            )
            await asyncio.sleep(min(available_delays))
            for monitor in self._monitors.values():
                await monitor._send_due_scheduled_notifications()
            if any(
                monitor._reset_confirmation_poll_due()
                for monitor in self._monitors.values()
            ):
                for monitor in self._monitors.values():
                    monitor._mark_reset_confirmation_polled()
                await self.check_once()
                next_poll_at = asyncio.get_running_loop().time() + self._poll_seconds
            elif asyncio.get_running_loop().time() >= next_poll_at:
                await self.check_once()
                next_poll_at = asyncio.get_running_loop().time() + self._poll_seconds

    async def check_once(self, *, report_limits: bool = False) -> bool:
        self._ensure_saved_account_monitors()
        notified_now = False
        for result in await self._reader.read_active_rate_limits():
            if report_limits:
                windows = extract_rate_limit_windows(
                    result.payload, limit_id=self._rate_limit_id
                )
                if windows:
                    LOGGER.info(
                        "Codex-Nutzungsstand beim Start für Konto %s:\n%s",
                        result.account_id,
                        "\n".join(_window_status(window) for window in windows),
                    )
                else:
                    LOGGER.warning(
                        "Der App-Server lieferte für Konto %s keine lesbaren "
                        "Codex-Nutzungsfenster.",
                        result.account_id,
                    )
            monitor = self._monitor_for(result.account_id, result.account_email)
            notified_now = await monitor.observe(result.payload) or notified_now
        return notified_now

    def _monitor_for(
        self, account_id: str, account_email: str | None = None
    ) -> CodexRateLimitMonitor:
        monitor = self._monitors.get(account_id)
        if monitor is None:
            monitor = CodexRateLimitMonitor(
                self._reader,  # Used only by the wrapper's direct observe calls.
                self._sink,
                state_path=self._state_path,
                rate_limit_id=self._rate_limit_id,
                poll_seconds=self._poll_seconds,
                account_id=account_id,
                account_email=account_email,
            )
            self._monitors[account_id] = monitor
        else:
            monitor.set_account_email(account_email)
        return monitor
