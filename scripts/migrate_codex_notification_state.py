"""Merge legacy Codex rate-limit state files into one state document."""

import argparse
import json
from pathlib import Path


def _read_state(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def migrate(state_path: Path) -> bool:
    primary = _read_state(state_path)
    if primary is None:
        return False
    if isinstance(primary.get("windows"), dict):
        return False

    weekly_path = state_path.with_name(f"{state_path.stem}-weekly{state_path.suffix}")
    weekly = _read_state(weekly_path)
    windows: dict[str, object] = {"five_hour": primary}
    if weekly is not None:
        windows["weekly"] = weekly

    backup_path = state_path.with_name(f"{state_path.stem}.before-window-merge{state_path.suffix}")
    if backup_path.exists():
        raise FileExistsError(f"Sicherungsdatei existiert bereits: {backup_path}")
    backup_path.write_text(
        json.dumps(primary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    state_path.write_text(
        json.dumps({"windows": windows}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Führt alte Codex-Rate-Limit-Zustandsdateien zusammen."
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path("data/codex-rate-limit.json"),
        help="Pfad der bisherigen 5-Stunden-Zustandsdatei.",
    )
    args = parser.parse_args()
    if migrate(args.state_path):
        print(f"Zustand zusammengeführt: {args.state_path}")
    else:
        print("Keine alte Zustandsdatei zu migrieren.")


if __name__ == "__main__":
    main()
