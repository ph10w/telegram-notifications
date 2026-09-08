"""Configure the Telegram bot shared by forwarding and notifications."""

import getpass
import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from tg_api.bot_gateway import TelegramBotGateway

from .errors import SetupError, TelegramBotApiError

TOKEN_VARIABLE = "TELEGRAM_NOTIFICATION_BOT_TOKEN"
FORWARDER_TARGET_VARIABLE = "TELEGRAM_TARGET_CHAT"
GENERAL_TARGET_VARIABLE = "TELEGRAM_GENERAL_NOTIFICATION_CHAT"
POLL_TIMEOUT_SECONDS = 30
SETUP_TIMEOUT_SECONDS = 180


def _configured_token(env_path: Path) -> str | None:
    environment_token = os.getenv(TOKEN_VARIABLE, "").strip()
    if environment_token:
        return environment_token
    value = dotenv_values(env_path).get(TOKEN_VARIABLE)
    return str(value).strip() if value else None


def _read_token(env_path: Path) -> str:
    existing = _configured_token(env_path)
    if existing:
        answer = input(f"Use the existing {TOKEN_VARIABLE} from .env? [Y/n] ").strip()
        if answer.lower() not in {"n", "no"}:
            return existing

    token = getpass.getpass(
        "Open the verified @BotFather in Telegram, send /newbot, and complete "
        "the bot creation.\nPaste the returned HTTP API token (input is hidden): "
    ).strip()
    if not token:
        raise SetupError("No bot token was entered.")
    return token


def _updated_env(contents: str, values: dict[str, str]) -> str:
    remaining = dict(values)
    output: list[str] = []
    for line in contents.splitlines(keepends=True):
        stripped = line.lstrip()
        replaced = False
        for name in tuple(remaining):
            if stripped.startswith(f"{name}="):
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                output.append(f"{name}={remaining.pop(name)}{newline}")
                replaced = True
                break
        if not replaced:
            output.append(line)

    if remaining:
        if output and not output[-1].endswith(("\n", "\r")):
            output[-1] += "\n"
        if output and output[-1].strip():
            output.append("\n")
        output.append("# Bot publisher shared by forwarding and notifications.\n")
        output.extend(f"{name}={value}\n" for name, value in remaining.items())
    return "".join(output)


def _write_env(env_path: Path, values: dict[str, str]) -> None:
    original = env_path.read_text(encoding="utf-8")
    updated = _updated_env(original, values)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=env_path.parent,
            prefix=f".{env_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(updated)
            temporary_path = Path(temporary.name)
        temporary_path.replace(env_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _configured_targets(env_path: Path) -> tuple[tuple[str, int | str], ...]:
    values = dotenv_values(env_path)
    targets: list[tuple[str, int | str]] = []
    for name in (FORWARDER_TARGET_VARIABLE, GENERAL_TARGET_VARIABLE):
        raw_target = values.get(name)
        if raw_target is None or not str(raw_target).strip():
            continue
        value = str(raw_target).strip()
        try:
            targets.append((name, int(value)))
        except ValueError:
            targets.append((name, value))
    if not targets:
        raise SetupError(
            f"At least one of {FORWARDER_TARGET_VARIABLE} or "
            f"{GENERAL_TARGET_VARIABLE} must be configured in .env."
        )
    return tuple(targets)


def configure_bot(env_path: Path) -> None:
    try:
        if not env_path.is_file():
            raise SetupError("Run this command from the project directory containing .env.")

        token = _read_token(env_path)
        bot = TelegramBotGateway(token)
        setup = bot.start_private_chat_setup()
        print(f"\nOpen this link and press Start:\n{setup.start_url}")
        print("Waiting up to 3 minutes for the private chat ...")
        bot.wait_for_private_start(
            setup,
            timeout_seconds=SETUP_TIMEOUT_SECONDS,
            poll_timeout_seconds=POLL_TIMEOUT_SECONDS,
        )

        for name, target_chat in _configured_targets(env_path):
            bot.ensure_can_publish(target_chat, setup.identity.id)
            print(f"Bot access to {name} verified.")

        _write_env(env_path, {TOKEN_VARIABLE: token})
        print("Configuration saved. The bot is ready for the configured targets.")
    except TelegramBotApiError as exc:
        raise SetupError(str(exc)) from exc
    except OSError as exc:
        raise SetupError(f"Could not update .env: {exc}") from exc
