import math
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .errors import NotificationConfigError
from .gemini_usage import (
    DEFAULT_GEMINI_USAGE_ARGS,
    DEFAULT_GEMINI_USAGE_COMMAND,
    DEFAULT_GEMINI_USAGE_GROUP,
)

type ChatRef = int | str


def parse_chat_ref(value: str) -> ChatRef:
    value = value.strip()
    if not value:
        raise NotificationConfigError(
            "Eine leere Telegram-Chat-Referenz ist nicht erlaubt."
        )
    try:
        return int(value)
    except ValueError:
        return value


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise NotificationConfigError(f"Die Umgebungsvariable {name} fehlt.")
    return value


def _number(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise NotificationConfigError(f"{name} muss eine Zahl sein.") from exc
    if not math.isfinite(value) or value < minimum:
        raise NotificationConfigError(f"{name} muss mindestens {minimum:g} sein.")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise NotificationConfigError(f"{name} muss ein Wahrheitswert sein.")


def _load_environment() -> Path | None:
    dotenv_file = find_dotenv()
    if not dotenv_file:
        load_dotenv()
        return None

    dotenv_path = Path(dotenv_file).resolve()
    load_dotenv(dotenv_path)
    return dotenv_path.parent


def _runtime_path(name: str, default: str, dotenv_directory: Path | None) -> Path:
    path = Path(os.getenv(name, default))
    if path.is_absolute():
        return path

    current_directory = Path.cwd().resolve()
    if dotenv_directory is None:
        raise NotificationConfigError(
            f"{name} ist relativ ({path}), aber in {current_directory} wurde keine "
            ".env gefunden. Führe den Befehl aus dem Verzeichnis der .env aus "
            "oder verwende einen absoluten Pfad."
        )
    if current_directory != dotenv_directory:
        raise NotificationConfigError(
            f"{name} ist relativ ({path}), aber die geladene .env liegt in "
            f"{dotenv_directory}. Führe den Befehl aus diesem Verzeichnis aus "
            "oder verwende einen absoluten Pfad."
        )
    return path


def _app_server_command() -> tuple[str, ...]:
    executable = os.getenv("CODEX_APP_SERVER_EXECUTABLE", "").strip()
    if executable:
        return executable, "app-server"

    raw = os.getenv("CODEX_APP_SERVER_COMMAND", "codex app-server").strip()
    try:
        command = tuple(shlex.split(raw))
    except ValueError as exc:
        raise NotificationConfigError(
            "CODEX_APP_SERVER_COMMAND enthält ungültige Anführungszeichen."
        ) from exc
    if not command:
        raise NotificationConfigError(
            "CODEX_APP_SERVER_COMMAND darf nicht leer sein."
        )
    return command


def _gemini_usage_command() -> tuple[str, ...]:
    executable = os.getenv("GEMINI_USAGE_EXECUTABLE", "").strip()
    if executable:
        return executable, *DEFAULT_GEMINI_USAGE_ARGS

    raw = os.getenv("GEMINI_USAGE_COMMAND", "").strip()
    if not raw:
        return DEFAULT_GEMINI_USAGE_COMMAND
    try:
        command = tuple(shlex.split(raw))
    except ValueError as exc:
        raise NotificationConfigError(
            "GEMINI_USAGE_COMMAND enthält ungültige Anführungszeichen."
        ) from exc
    if not command:
        raise NotificationConfigError("GEMINI_USAGE_COMMAND darf nicht leer sein.")
    return command


def _gemini_state_path(
    codex_state_path: Path, dotenv_directory: Path | None
) -> Path:
    if os.getenv("GEMINI_NOTIFICATION_STATE", "").strip():
        return _runtime_path(
            "GEMINI_NOTIFICATION_STATE",
            "data/gemini-rate-limit.json",
            dotenv_directory,
        )
    return codex_state_path.parent / "gemini-rate-limit.json"


@dataclass(frozen=True, slots=True)
class NotificationConfig:
    bot_token: str
    target_chat: ChatRef
    codex_app_server_command: tuple[str, ...]
    codex_rate_limit_id: str
    codex_poll_seconds: float
    state_path: Path
    log_path: Path
    log_level: str
    gemini_enabled: bool = True
    gemini_usage_command: tuple[str, ...] = DEFAULT_GEMINI_USAGE_COMMAND
    gemini_usage_group: str = DEFAULT_GEMINI_USAGE_GROUP
    gemini_poll_seconds: float = 900.0
    gemini_state_path: Path = Path("data/gemini-rate-limit.json")

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        dotenv_directory = _load_environment()
        state_path = _runtime_path(
            "CODEX_NOTIFICATION_STATE",
            "data/codex-rate-limit.json",
            dotenv_directory,
        )
        return cls(
            bot_token=_required("TELEGRAM_NOTIFICATION_BOT_TOKEN"),
            target_chat=parse_chat_ref(
                _required("TELEGRAM_GENERAL_NOTIFICATION_CHAT")
            ),
            codex_app_server_command=_app_server_command(),
            codex_rate_limit_id=(
                os.getenv("CODEX_RATE_LIMIT_ID", "codex").strip() or "codex"
            ),
            codex_poll_seconds=_number(
                "CODEX_RATE_LIMIT_POLL_SECONDS", 900.0, minimum=1.0
            ),
            state_path=state_path,
            log_path=_runtime_path(
                "TELEGRAM_NOTIFICATION_LOG",
                "data/logs/telegram-notifications.log",
                dotenv_directory,
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            gemini_enabled=_bool("GEMINI_MONITOR_ENABLED", True),
            gemini_usage_command=_gemini_usage_command(),
            gemini_usage_group=(
                os.getenv("GEMINI_USAGE_GROUP", DEFAULT_GEMINI_USAGE_GROUP).strip()
                or DEFAULT_GEMINI_USAGE_GROUP
            ),
            gemini_poll_seconds=_number(
                "GEMINI_RATE_LIMIT_POLL_SECONDS", 900.0, minimum=1.0
            ),
            gemini_state_path=_gemini_state_path(state_path, dotenv_directory),
        )
