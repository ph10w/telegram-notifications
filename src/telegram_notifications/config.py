import math
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .errors import NotificationConfigError

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

    @classmethod
    def from_env(cls) -> "NotificationConfig":
        dotenv_directory = _load_environment()
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
                "CODEX_RATE_LIMIT_POLL_SECONDS", 1800.0, minimum=1.0
            ),
            state_path=_runtime_path(
                "CODEX_NOTIFICATION_STATE",
                "data/codex-rate-limit.json",
                dotenv_directory,
            ),
            log_path=_runtime_path(
                "TELEGRAM_NOTIFICATION_LOG",
                "data/logs/telegram-notifications.log",
                dotenv_directory,
            ),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )
