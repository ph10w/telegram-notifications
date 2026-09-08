import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

type ChatRef = int | str

_AUTHOR_ID_PATTERN = re.compile(r"[+-]?\d+")
_AUTHOR_USERNAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{3,31}")


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


def parse_chat_ref(value: str) -> ChatRef:
    value = value.strip()
    if not value:
        raise ConfigError("Eine leere Telegram-Chat-Referenz ist nicht erlaubt.")
    try:
        return int(value)
    except ValueError:
        return value


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Die Umgebungsvariable {name} fehlt.")
    return value


def _integer(name: str, default: int | None = None, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        if default is None:
            raise ConfigError(f"Die Umgebungsvariable {name} fehlt.")
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein.") from exc
    if value < minimum:
        raise ConfigError(f"{name} muss mindestens {minimum} sein.")
    return value


def _number(name: str, default: float = 0.0, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine Zahl sein.") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigError(f"{name} muss mindestens {minimum:g} sein.")
    return value


def _boolean(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} muss true oder false sein.")


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
        raise ConfigError(
            f"{name} ist relativ ({path}), aber in {current_directory} wurde keine "
            ".env gefunden. Führe den Befehl aus dem Verzeichnis der .env aus "
            "oder verwende einen absoluten Pfad."
        )
    if current_directory != dotenv_directory:
        raise ConfigError(
            f"{name} ist relativ ({path}), aber die geladene .env liegt in "
            f"{dotenv_directory}. Führe den Befehl aus diesem Verzeichnis aus "
            "oder verwende einen absoluten Pfad."
        )
    return path


def _notification_bot_token() -> str:
    return _required("TELEGRAM_NOTIFICATION_BOT_TOKEN")


def _duration_exempt_authors() -> frozenset[str]:
    raw = os.getenv("MIN_VOICE_DURATION_EXEMPT_AUTHORS", "")
    authors: set[str] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if _AUTHOR_ID_PATTERN.fullmatch(item):
            authors.add(f"sender:{int(item)}")
            continue
        username = item.removeprefix("@").casefold()
        if not _AUTHOR_USERNAME_PATTERN.fullmatch(username):
            raise ConfigError(
                "MIN_VOICE_DURATION_EXEMPT_AUTHORS muss durch Kommas getrennte "
                "Telegram-User-IDs oder @Usernames enthalten."
            )
        authors.add(f"username:{username}")
    return frozenset(authors)


@dataclass(frozen=True, slots=True)
class BaseConfig:
    api_id: int
    api_hash: str
    phone: str | None
    session_path: Path
    state_db: Path
    log_level: str
    entity_cache_limit: int

    @classmethod
    def from_env(cls) -> "BaseConfig":
        dotenv_directory = _load_environment()
        session_path = _runtime_path(
            "TELEGRAM_SESSION", "data/telegram-monitor", dotenv_directory
        )
        state_db = _runtime_path("STATE_DB", "data/forwarder.sqlite3", dotenv_directory)
        phone = os.getenv("TELEGRAM_PHONE", "").strip() or None
        return cls(
            api_id=_integer("TELEGRAM_API_ID", minimum=1),
            api_hash=_required("TELEGRAM_API_HASH"),
            phone=phone,
            session_path=session_path,
            state_db=state_db,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            entity_cache_limit=_integer("TELETHON_ENTITY_CACHE_LIMIT", 500, minimum=100),
        )


@dataclass(frozen=True, slots=True)
class ForwarderConfig(BaseConfig):
    source_chats: tuple[ChatRef, ...]
    target_chat: ChatRef
    initial_scan_limit: int
    min_voice_duration_seconds: float
    include_video_notes: bool
    notification_bot_token: str = field(default="", repr=False)
    duration_exempt_authors: frozenset[str] = frozenset()

    def is_duration_exempt_author(
        self, author_identifiers: frozenset[str] | None
    ) -> bool:
        return bool(author_identifiers) and not author_identifiers.isdisjoint(
            self.duration_exempt_authors
        )

    @classmethod
    def from_env(cls) -> "ForwarderConfig":
        base = BaseConfig.from_env()
        notification_bot_token = _notification_bot_token()
        raw_sources = _required("TELEGRAM_SOURCE_CHATS")
        sources = tuple(parse_chat_ref(item) for item in raw_sources.split(",") if item.strip())
        if not sources:
            raise ConfigError("TELEGRAM_SOURCE_CHATS enthält keine Quelle.")
        return cls(
            api_id=base.api_id,
            api_hash=base.api_hash,
            phone=base.phone,
            session_path=base.session_path,
            state_db=base.state_db,
            log_level=base.log_level,
            entity_cache_limit=base.entity_cache_limit,
            source_chats=sources,
            target_chat=parse_chat_ref(_required("TELEGRAM_TARGET_CHAT")),
            initial_scan_limit=_integer("INITIAL_SCAN_LIMIT", 100),
            min_voice_duration_seconds=_number("MIN_VOICE_DURATION_SECONDS"),
            include_video_notes=_boolean("INCLUDE_VIDEO_NOTES", False),
            notification_bot_token=notification_bot_token,
            duration_exempt_authors=_duration_exempt_authors(),
        )
