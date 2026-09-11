import json
import tempfile
import unittest
from pathlib import Path

from tg_notification.codex_accounts import CodexAccountProfileStore


def _auth(account_id: str, last_refresh: str) -> dict[str, object]:
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "account_id": account_id,
        },
        "last_refresh": last_refresh,
    }


def _write_auth(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class CodexAccountProfileStoreTests(unittest.TestCase):
    def test_creates_a_file_backed_profile_for_the_current_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_home = root / "source"
            profiles_root = root / "profiles"
            _write_auth(
                source_home / "auth.json",
                _auth("account-a", "2026-09-11T12:00:00Z"),
            )

            profiles = CodexAccountProfileStore(source_home, profiles_root).profiles()

            self.assertEqual([profile.account_id for profile in profiles], ["account-a"])
            self.assertEqual(
                json.loads(profiles[0].auth_path.read_text(encoding="utf-8"))["tokens"]["account_id"],
                "account-a",
            )
            self.assertEqual(
                (profiles[0].home / "config.toml").read_text(encoding="utf-8"),
                'cli_auth_credentials_store = "file"\n',
            )

    def test_replaces_a_profile_only_when_current_auth_is_newer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_home = root / "source"
            profiles_root = root / "profiles"
            profile_auth_path = profiles_root / "account-a" / "auth.json"
            _write_auth(
                source_home / "auth.json",
                _auth("account-a", "2026-09-11T11:00:00Z"),
            )
            _write_auth(
                profile_auth_path,
                _auth("account-a", "2026-09-11T12:00:00Z"),
            )
            store = CodexAccountProfileStore(source_home, profiles_root)

            store.profiles()

            self.assertEqual(
                json.loads(profile_auth_path.read_text(encoding="utf-8"))["last_refresh"],
                "2026-09-11T12:00:00Z",
            )
            _write_auth(
                source_home / "auth.json",
                _auth("account-a", "2026-09-11T13:00:00Z"),
            )

            store.profiles()

            self.assertEqual(
                json.loads(profile_auth_path.read_text(encoding="utf-8"))["last_refresh"],
                "2026-09-11T13:00:00Z",
            )

    def test_keeps_other_account_profiles_when_current_account_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_home = root / "source"
            profiles_root = root / "profiles"
            store = CodexAccountProfileStore(source_home, profiles_root)
            _write_auth(
                source_home / "auth.json",
                _auth("account-a", "2026-09-11T12:00:00Z"),
            )
            store.profiles()
            _write_auth(
                source_home / "auth.json",
                _auth("account-b", "2026-09-11T13:00:00Z"),
            )

            profiles = store.profiles()

            self.assertEqual(
                [profile.account_id for profile in profiles], ["account-a", "account-b"]
            )
