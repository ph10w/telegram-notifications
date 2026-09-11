"""Persist and isolate file-backed Codex credentials per ChatGPT account."""

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import CodexAppServerError

LOGGER = logging.getLogger(__name__)
AUTH_FILE_NAME = "auth.json"
CONFIG_FILE_NAME = "config.toml"


@dataclass(frozen=True, slots=True)
class CodexAccountProfile:
    account_id: str
    home: Path

    @property
    def auth_path(self) -> Path:
        return self.home / AUTH_FILE_NAME


def _account_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    account_id = tokens.get("account_id")
    return account_id.strip() if isinstance(account_id, str) and account_id.strip() else None


def _last_refresh(payload: object) -> float | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("last_refresh")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _read_auth(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) and _account_id(payload) else None


def _write_private_file(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


class CodexAccountProfileStore:
    """Maintains one file-backed ``CODEX_HOME`` directory per account."""

    def __init__(self, source_home: Path, profiles_root: Path) -> None:
        self._source_auth_path = source_home / AUTH_FILE_NAME
        self._profiles_root = profiles_root

    def synchronize_current_account(self) -> CodexAccountProfile | None:
        source_payload = _read_auth(self._source_auth_path)
        if source_payload is None:
            LOGGER.warning(
                "Die aktuelle Codex-Authentifizierung konnte nicht gelesen werden: %s",
                self._source_auth_path,
            )
            return None

        account_id = _account_id(source_payload)
        assert account_id is not None
        profile = CodexAccountProfile(account_id, self._profiles_root / account_id)
        target_payload = _read_auth(profile.auth_path)
        source_refresh = _last_refresh(source_payload)
        target_refresh = _last_refresh(target_payload)
        should_copy = target_payload is None or (
            source_refresh is not None
            and target_refresh is not None
            and source_refresh > target_refresh
        )
        if should_copy:
            try:
                _write_private_file(
                    profile.auth_path,
                    json.dumps(source_payload, indent=2, sort_keys=True) + "\n",
                )
            except OSError as exc:
                raise CodexAppServerError(
                    "Die Codex-Authentifizierung konnte nicht im Konto-Profil "
                    f"gespeichert werden: {exc}"
                ) from exc
        self._ensure_file_credential_store(profile)
        return profile

    def profiles(self) -> tuple[CodexAccountProfile, ...]:
        self.synchronize_current_account()
        try:
            directories = tuple(self._profiles_root.iterdir())
        except FileNotFoundError:
            return ()
        except OSError as exc:
            raise CodexAppServerError(
                f"Die Codex-Konto-Profile konnten nicht gelesen werden: {exc}"
            ) from exc

        profiles: list[CodexAccountProfile] = []
        for directory in directories:
            if not directory.is_dir():
                continue
            payload = _read_auth(directory / AUTH_FILE_NAME)
            account_id = _account_id(payload)
            if account_id is None or directory.name != account_id:
                LOGGER.warning("Ungültiges Codex-Konto-Profil wird übersprungen: %s", directory)
                continue
            profile = CodexAccountProfile(account_id, directory)
            self._ensure_file_credential_store(profile)
            profiles.append(profile)
        return tuple(sorted(profiles, key=lambda profile: profile.account_id))

    @staticmethod
    def source_home_from_environment() -> Path:
        configured_home = os.getenv("CODEX_HOME", "").strip()
        return Path(configured_home) if configured_home else Path.home() / ".codex"

    @staticmethod
    def _ensure_file_credential_store(profile: CodexAccountProfile) -> None:
        config_path = profile.home / CONFIG_FILE_NAME
        required_setting = 'cli_auth_credentials_store = "file"\n'
        try:
            current = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            _write_private_file(config_path, required_setting)
            return
        except OSError as exc:
            raise CodexAppServerError(
                f"Die Codex-Profilkonfiguration konnte nicht gelesen werden: {exc}"
            ) from exc
        if current != required_setting:
            _write_private_file(config_path, required_setting)
