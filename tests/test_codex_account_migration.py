import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "migrate_codex_notification_accounts.py"
)
SPEC = importlib.util.spec_from_file_location("account_migration", SCRIPT_PATH)
assert SPEC is not None
assert SPEC.loader is not None
ACCOUNT_MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCOUNT_MIGRATION)


class CodexAccountMigrationTests(unittest.TestCase):
    def test_migrates_legacy_windows_to_the_active_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                json.dumps(
                    {
                        "tokens": {"account_id": "account-a"},
                        "last_refresh": "2026-09-11T12:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            state_path = root / "codex-rate-limit.json"
            state_path.write_text(
                json.dumps({"windows": {"five_hour": {"used_percent": 50}}}),
                encoding="utf-8",
            )

            migrated = ACCOUNT_MIGRATION.migrate(state_path, codex_home)

            self.assertTrue(migrated)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {
                    "accounts": {
                        "account-a": {
                            "windows": {"five_hour": {"used_percent": 50}}
                        }
                    }
                },
            )
            self.assertTrue(
                (root / "codex-rate-limit.before-account-migration.json").exists()
            )

    def test_skips_state_that_is_already_grouped_by_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "codex-rate-limit.json"
            state_path.write_text(
                json.dumps({"accounts": {"account-a": {"windows": {}}}}),
                encoding="utf-8",
            )

            migrated = ACCOUNT_MIGRATION.migrate(state_path, root / "missing")

            self.assertFalse(migrated)
