import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .bot_setup import configure_bot
from .config import AccountConfig, ConfigError
from .errors import SetupError
from .service import list_available_chats


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-setup",
        description="Telegram für Forwarder und Benachrichtigungen einrichten.",
    )
    parser.add_argument(
        "command",
        choices=("list-chats", "configure-bot"),
        help="Chat-IDs anzeigen oder den gemeinsamen Bot einrichten.",
    )
    return parser


def _configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        raise ConfigError(f"Unbekanntes LOG_LEVEL: {level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    command = _parser().parse_args().command
    try:
        if command == "configure-bot":
            configure_bot(Path.cwd() / ".env")
            return

        config = AccountConfig.from_env()
        _configure_logging(config.log_level)
        dialogs = asyncio.run(list_available_chats(config))
        print(f"{'ID':>16}  {'Typ':<10}  Name")
        print(f"{'-' * 16}  {'-' * 10}  {'-' * 40}")
        for dialog in dialogs:
            print(f"{dialog.id:>16}  {dialog.kind:<10}  {dialog.name}")
    except (ConfigError, ValueError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except SetupError as exc:
        print(f"Setup-Fehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
