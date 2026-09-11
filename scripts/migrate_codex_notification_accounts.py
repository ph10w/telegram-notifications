"""Assign a legacy Codex rate-limit state document to its signed-in account."""

import argparse
import json
from pathlib import Path

from tg_notification.codex_accounts import CodexAccountProfileStore


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def migrate(state_path: Path, codex_home: Path) -> bool:
    """Move an ungrouped state document into the currently signed-in account."""
    payload = _read_state(state_path)
    if payload is None or isinstance(payload.get("accounts"), dict):
        return False
    windows = payload.get("windows")
    if not isinstance(windows, dict):
        return False

    profile_store = CodexAccountProfileStore(
        codex_home, state_path.parent / "codex-accounts"
    )
    profile = profile_store.synchronize_current_account()
    if profile is None:
        raise RuntimeError("Kein angemeldetes Codex-Konto in auth.json gefunden.")

    backup_path = state_path.with_name(
        f"{state_path.stem}.before-account-migration{state_path.suffix}"
    )
    if backup_path.exists():
        raise FileExistsError(f"Sicherungsdatei existiert bereits: {backup_path}")
    backup_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    migrated = {"accounts": {profile.account_id: {"windows": windows}}}
    temporary = state_path.with_name(f".{state_path.name}.tmp")
    temporary.write_text(
        json.dumps(migrated, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(state_path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ordnet den bisherigen Codex-Rate-Limit-Zustand einem Konto zu."
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("data/codex-rate-limit.json"),
        help="Pfad der bisherigen Zustandsdatei.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=CodexAccountProfileStore.source_home_from_environment(),
        help="CODEX_HOME mit der derzeit aktiven auth.json.",
    )
    args = parser.parse_args()
    if migrate(args.state_path, args.codex_home):
        print(f"Zustand dem aktiven Konto zugeordnet: {args.state_path}")
    else:
        print("Keine nicht gruppierte Zustandsdatei zu migrieren.")


if __name__ == "__main__":
    main()
