import argparse
import asyncio
import logging
import re
import sys
from datetime import timedelta

from .bootstrap import reset_forwarder, run_monitoring
from .config import ChatRef, ConfigError, ForwarderConfig, parse_chat_ref
from .errors import TelegramServiceError

RESET_PERIOD_PATTERN = re.compile(r"^(?P<amount>[1-9]\d*)(?P<unit>[HDW])$", re.IGNORECASE)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-forwarder",
        description="Telegram-Gruppen überwachen und Sprachnachrichten weiterleiten.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        help=(
            "Monitoring starten oder Scan-Zustand vollständig beziehungsweise "
            "zeitlich begrenzt zurücksetzen, z. B. reset=1W (Standard: run)."
        ),
    )
    parser.add_argument(
        "--source",
        type=parse_chat_ref,
        metavar="CHAT",
        help=(
            "Reset auf einen Source-Chat begrenzen, z. B. "
            "--source=-1001234567890 oder --source=@gruppe."
        ),
    )
    return parser


def parse_command(value: str) -> tuple[str, timedelta | None]:
    if value == "run":
        return value, None
    if value == "reset":
        return value, None
    if value.lower().startswith("reset="):
        raw_period = value.partition("=")[2]
        match = RESET_PERIOD_PATTERN.fullmatch(raw_period)
        if not match:
            raise ConfigError(
                "Reset-Zeitraum muss eine positive ganze Zahl mit H, D oder W sein, "
                "z. B. reset=24H, reset=7D oder reset=1W."
            )
        amount = int(match.group("amount"))
        unit = match.group("unit").upper()
        try:
            if unit == "H":
                return "reset", timedelta(hours=amount)
            if unit == "D":
                return "reset", timedelta(days=amount)
            return "reset", timedelta(weeks=amount)
        except OverflowError as exc:
            raise ConfigError("Reset-Zeitraum ist zu groß.") from exc
    raise ConfigError(f"Unbekannter Befehl: {value}")


def _configure_logging(level: str) -> None:
    numeric_level = getattr(logging, level, None)
    if not isinstance(numeric_level, int):
        raise ConfigError(f"Unbekanntes LOG_LEVEL: {level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    args = _parser().parse_args()
    try:
        command, reset_period = parse_command(args.command)
        source_chat: ChatRef | None = args.source
        if source_chat is not None and command != "reset":
            raise ConfigError("--source kann nur mit reset verwendet werden.")
        if command == "reset":
            config = ForwarderConfig.from_env()
            _configure_logging(config.log_level)
            result = asyncio.run(
                reset_forwarder(config, reset_period, source_chat)
            )
            scope = f" für Quelle {source_chat}" if source_chat is not None else ""
            if result.cutoff is None:
                print(
                    f"Scan-Zustand{scope} zurückgesetzt ({result.state_db}): "
                    f"{result.cursor_count} Cursor und "
                    f"{result.history_count} bekannte Nachrichten gelöscht."
                )
            else:
                print(
                    f"Scan-Zustand{scope} seit "
                    f"{result.cutoff.astimezone():%Y-%m-%d %H:%M:%S %Z} "
                    f"zurückgesetzt ({result.state_db}): "
                    f"{result.cursor_count} Cursor zurückgesetzt und "
                    f"{result.history_count} bekannte Nachrichten gelöscht."
                )
            print(
                f"{result.deleted_target_count} zugehörige Nachricht(en) "
                f"im Zielchat gelöscht."
            )
            if result.unavailable_target_count:
                print(
                    f"WARNUNG: {result.unavailable_target_count} ältere oder einem "
                    f"anderen Zielchat zugeordnete Nachricht(en) konnten nicht "
                    f"automatisch gelöscht werden.",
                    file=sys.stderr,
                )
        else:
            config = ForwarderConfig.from_env()
            _configure_logging(config.log_level)
            asyncio.run(run_monitoring(config))
    except (ConfigError, ValueError) as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except TelegramServiceError as exc:
        print(f"Telegram-Fehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        print("Monitoring beendet.")


if __name__ == "__main__":
    main()
