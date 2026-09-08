import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv


class ConfigError(ValueError):
    """Raised when setup configuration is missing or invalid."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Die Umgebungsvariable {name} fehlt.")
    return value


def _integer(name: str, *, minimum: int) -> int:
    try:
        value = int(_required(name))
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein.") from exc
    if value < minimum:
        raise ConfigError(f"{name} muss mindestens {minimum} sein.")
    return value


def _load_environment() -> Path | None:
    dotenv_file = find_dotenv()
    if not dotenv_file:
        load_dotenv()
        return None

    dotenv_path = Path(dotenv_file).resolve()
    load_dotenv(dotenv_path)
    return dotenv_path.parent


def _session_path(dotenv_directory: Path | None) -> Path:
    path = Path(os.getenv("TELEGRAM_SESSION", "data/telegram-monitor"))
    if path.is_absolute():
        return path

    current_directory = Path.cwd().resolve()
    if dotenv_directory is None:
        raise ConfigError(
            f"TELEGRAM_SESSION ist relativ ({path}), aber in {current_directory} "
            "wurde keine .env gefunden. Führe den Befehl aus dem Verzeichnis "
            "der .env aus oder verwende einen absoluten Pfad."
        )
    if current_directory != dotenv_directory:
        raise ConfigError(
            f"TELEGRAM_SESSION ist relativ ({path}), aber die geladene .env "
            f"liegt in {dotenv_directory}. Führe den Befehl aus diesem "
            "Verzeichnis aus oder verwende einen absoluten Pfad."
        )
    return path


@dataclass(frozen=True, slots=True)
class AccountConfig:
    api_id: int
    api_hash: str
    phone: str | None
    session_path: Path
    log_level: str
    entity_cache_limit: int

    @classmethod
    def from_env(cls) -> "AccountConfig":
        dotenv_directory = _load_environment()
        raw_cache_limit = os.getenv("TELETHON_ENTITY_CACHE_LIMIT", "500").strip()
        try:
            entity_cache_limit = int(raw_cache_limit)
        except ValueError as exc:
            raise ConfigError("TELETHON_ENTITY_CACHE_LIMIT muss eine ganze Zahl sein.") from exc
        if entity_cache_limit < 100:
            raise ConfigError("TELETHON_ENTITY_CACHE_LIMIT muss mindestens 100 sein.")
        return cls(
            api_id=_integer("TELEGRAM_API_ID", minimum=1),
            api_hash=_required("TELEGRAM_API_HASH"),
            phone=os.getenv("TELEGRAM_PHONE", "").strip() or None,
            session_path=_session_path(dotenv_directory),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
            entity_cache_limit=entity_cache_limit,
        )
