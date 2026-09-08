#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="telegram-voice-forwarder.service"
DISPLAY_NAME="Telegram Voice Forwarder"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
UNIT_FILE="/etc/systemd/system/$SERVICE_NAME"
ENV_FILE="$PROJECT_DIR/.env"
VENV_DIR="$PROJECT_DIR/.venv"
PYTHON_EXE="$VENV_DIR/bin/python"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
UNIT_TEMP=""

cleanup() {
    if [[ -n "$UNIT_TEMP" && -f "$UNIT_TEMP" ]]; then
        rm -f -- "$UNIT_TEMP"
    fi
}
trap cleanup EXIT

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

cd -- "$PROJECT_DIR" || fail "Could not change to $PROJECT_DIR."

if [[ "$SERVICE_USER" == "root" ]]; then
    fail "Refusing to run the Telegram service as root. Set SERVICE_USER to a regular user."
fi
if ! getent passwd "$SERVICE_USER" >/dev/null; then
    fail "Service user '$SERVICE_USER' does not exist."
fi
SERVICE_GROUP="$(id -gn "$SERVICE_USER")"

if (( EUID == 0 )); then
    ROOT=()
else
    command -v sudo >/dev/null || fail "sudo is required for package and service installation."
    ROOT=(sudo)
fi

run_as_service_user() {
    if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
        "$@"
    elif command -v sudo >/dev/null; then
        sudo -H -u "$SERVICE_USER" -- "$@"
    elif (( EUID == 0 )) && command -v runuser >/dev/null; then
        runuser -u "$SERVICE_USER" -- "$@"
    else
        fail "Cannot run commands as service user $SERVICE_USER."
    fi
}

systemd_quote() {
    local value="$1"
    value="${value//%/%%}"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "$value"
}

printf 'Installing %s from:\n  %s\n' "$DISPLAY_NAME" "$PROJECT_DIR"
printf 'Service account:\n  %s:%s\n\n' "$SERVICE_USER" "$SERVICE_GROUP"

command -v apt-get >/dev/null || fail "apt-get was not found; this installer requires Raspberry Pi OS or Debian."
command -v dpkg-query >/dev/null || fail "dpkg-query was not found; this installer requires Raspberry Pi OS or Debian."
command -v systemctl >/dev/null || fail "systemctl was not found; Raspberry Pi OS must use systemd."

if [[ -e "$UNIT_FILE" ]] || systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
    fail "Service $SERVICE_NAME already exists. Remove it before running this installer again."
fi
if [[ ! -f "$ENV_FILE" ]]; then
    fail "Configuration file $ENV_FILE was not found. Copy .env.example to .env and configure it first."
fi
run_as_service_user test -r "$ENV_FILE" || fail "$SERVICE_USER cannot read $ENV_FILE."

packages=()
if ! command -v python3 >/dev/null; then
    packages+=(python3)
fi
if ! dpkg-query -W -f='${Status}' python3-venv 2>/dev/null \
    | grep -qx 'install ok installed'; then
    packages+=(python3-venv)
fi

if (( ${#packages[@]} > 0 )); then
    printf 'Installing missing packages: %s\n' "${packages[*]}"
    "${ROOT[@]}" apt-get update
    "${ROOT[@]}" apt-get install -y --no-install-recommends "${packages[@]}"
fi

python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 13))' \
    || fail "Python 3.13 or newer is required. Upgrade Raspberry Pi OS before continuing."

if [[ ! -x "$PYTHON_EXE" ]]; then
    if [[ -e "$VENV_DIR" ]]; then
        fail "$VENV_DIR exists but does not contain a working Python environment."
    fi
    printf 'Creating virtual environment...\n'
    run_as_service_user python3 -m venv "$VENV_DIR"
fi

printf 'Installing the project and Python dependencies...\n'
run_as_service_user "$PYTHON_EXE" -m pip install --disable-pip-version-check -e "$PROJECT_DIR"

mapfile -t runtime_paths < <(
    run_as_service_user "$PYTHON_EXE" -c '
from pathlib import Path
from tg_forwarder.config import ForwarderConfig

config = ForwarderConfig.from_env()
session = config.session_path
if session.suffix != ".session":
    session = Path(f"{session}.session")
print(session.resolve())
print(config.state_db.resolve().parent)
'
)
(( ${#runtime_paths[@]} == 2 )) || fail "Could not resolve runtime paths from .env."
SESSION_FILE="${runtime_paths[0]}"
STATE_DIR="${runtime_paths[1]}"

run_as_service_user mkdir -p -- "$(dirname -- "$SESSION_FILE")" "$STATE_DIR"
if [[ ! -f "$SESSION_FILE" ]]; then
    [[ -t 0 ]] || fail "Telegram session $SESSION_FILE is missing and interactive login is unavailable."
    printf '\nNo authorized Telegram session was found.\n'
    printf 'Starting interactive Telegram login and chat listing...\n\n'
    run_as_service_user "$PYTHON_EXE" -m tg_setup list-chats
fi
[[ -f "$SESSION_FILE" ]] || fail "Telegram login did not create $SESSION_FILE."
run_as_service_user test -r "$SESSION_FILE" || fail "$SERVICE_USER cannot read $SESSION_FILE."
run_as_service_user test -w "$SESSION_FILE" || fail "$SERVICE_USER cannot write $SESSION_FILE."
run_as_service_user test -w "$STATE_DIR" || fail "$SERVICE_USER cannot write $STATE_DIR."

UNIT_TEMP="$(mktemp)"
cat >"$UNIT_TEMP" <<EOF
[Unit]
Description=$DISPLAY_NAME
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$(systemd_quote "$PYTHON_EXE") -m tg_forwarder run
Restart=always
RestartSec=5s
TimeoutStopSec=15s
KillMode=control-group
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
StandardOutput=journal
StandardError=journal
SyslogIdentifier=telegram-voice-forwarder

[Install]
WantedBy=multi-user.target
EOF

printf 'Installing systemd service...\n'
"${ROOT[@]}" install -o root -g root -m 0644 "$UNIT_TEMP" "$UNIT_FILE"
"${ROOT[@]}" systemctl daemon-reload
"${ROOT[@]}" systemctl enable "$SERVICE_NAME"

printf 'Starting service...\n'
if ! "${ROOT[@]}" systemctl start "$SERVICE_NAME"; then
    "${ROOT[@]}" journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    fail "The service could not be started."
fi
sleep 2
if ! "${ROOT[@]}" systemctl is-active --quiet "$SERVICE_NAME"; then
    "${ROOT[@]}" systemctl status "$SERVICE_NAME" --no-pager || true
    "${ROOT[@]}" journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
    fail "The service stopped immediately after startup."
fi

printf '\n%s was installed and started successfully.\n' "$DISPLAY_NAME"
printf 'Service: %s\n' "$SERVICE_NAME"
printf 'Status:  sudo systemctl status %s\n' "$SERVICE_NAME"
printf 'Logs:    sudo journalctl -u %s -f\n' "$SERVICE_NAME"
