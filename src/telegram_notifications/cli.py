import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .codex_app_server import CodexAppServerRateLimitReader
from .codex_monitor import CodexRateLimitMonitor
from .config import NotificationConfig, NotificationConfigError
from .errors import CodexAppServerError, TelegramNotificationError
from .sinks import TelegramNotificationSink
from .telegram_api import TelegramBotApi


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram-notifications",
        description="Allgemeine Telegram-Benachrichtigungen ausführen.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        help=(
            "Codex-Nutzungsmonitor starten (Standard), eine Testmeldung senden "
            "oder mit send eine eigene Textmeldung senden."
        ),
    )
    parser.add_argument("message", nargs="*", help="Text für den send-Befehl.")
    return parser


def _configure_logging(level: str, log_path: Path) -> None:
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        raise NotificationConfigError(f"Unbekanntes LOG_LEVEL: {level}")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
    except OSError as exc:
        raise NotificationConfigError(
            f"Benachrichtigungslog kann nicht geöffnet werden: {log_path}"
        ) from exc
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[handler],
    )


async def _send_text(config: NotificationConfig, text: str) -> None:
    sink = TelegramNotificationSink(
        TelegramBotApi(config.bot_token), config.target_chat
    )
    await sink.send_text(text)


def main() -> None:
    args = _parser().parse_args()
    try:
        config = NotificationConfig.from_env()
        _configure_logging(config.log_level, config.log_path)
        if args.command == "send-test":
            asyncio.run(
                _send_text(
                    config,
                    "Telegram-Benachrichtigungen sind korrekt konfiguriert.",
                )
            )
        elif args.command == "send":
            if not args.message:
                raise NotificationConfigError(
                    "Der Befehl send benötigt mindestens ein Textargument."
                )
            asyncio.run(_send_text(config, " ".join(args.message)))
        elif args.command == "run":
            sink = TelegramNotificationSink(
                TelegramBotApi(config.bot_token), config.target_chat
            )
            monitor = CodexRateLimitMonitor(
                CodexAppServerRateLimitReader(config.codex_app_server_command),
                sink,
                state_path=config.state_path,
                rate_limit_id=config.codex_rate_limit_id,
                poll_seconds=config.codex_poll_seconds,
            )
            asyncio.run(monitor.run())
        else:
            raise NotificationConfigError(f"Unbekannter Befehl: {args.command}")
    except (NotificationConfigError, ValueError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except (CodexAppServerError, TelegramNotificationError) as exc:
        print(f"Benachrichtigungsfehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("Benachrichtigungsmonitor beendet.")


if __name__ == "__main__":
    main()
