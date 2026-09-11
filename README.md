# Telegram Forwarder and Notifications

Two runtime packages and one setup package share Telegram access through
`tg_api`:

- `tg-forwarder` monitors Telegram sources and forwards voice messages.
- `tg-notification` sends general notifications and monitors Codex/Work usage.
- `tg-setup` performs interactive Telegram account and bot provisioning.

## Requirements

- Python 3.13+
- A Telegram application from [my.telegram.org/apps](https://my.telegram.org/apps)
- A bot created with [@BotFather](https://t.me/BotFather)
- Access to the configured source chats and permission to forward their content

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` in `.env`, then list account
dialogs to obtain chat IDs. The first run requests Telegram login data and
creates the session file configured by `TELEGRAM_SESSION`.

```powershell
tg-setup list-chats
```

Set the source and target chats, add the bot to the
target-chat as an admin, then run the interactive bot setup:

```powershell
tg-setup configure-bot
```
The complete step-by-step setup and forwarding reset behavior are in
[docs/setup.md](docs/setup.md).

## Configuration

All settings are read from `.env`; relative paths require running commands from
the directory containing that file.

| Parameter | Used by | Purpose | Default |
| --- | --- | --- | --- |
| `TELEGRAM_NOTIFICATION_BOT_TOKEN` | Both / Setup | Bot token; the setup command stores it | — |
| `LOG_LEVEL` | Both | Logging level | `INFO` |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Forwarder / Setup | Telegram application credentials | — |
| `TELEGRAM_PHONE` | Forwarder / Setup | Phone number for first login; otherwise prompted | — |
| `TELEGRAM_SOURCE_CHATS` | Forwarder | Comma-separated numeric IDs or `@usernames` | — |
| `TELEGRAM_TARGET_CHAT` | Forwarder | Bot target chat | — |
| `TELEGRAM_SESSION` | Forwarder / Setup | Account session path | `data/telegram-monitor` |
| `STATE_DB` | Forwarder | Forwarder SQLite state | `data/forwarder.sqlite3` |
| `TELETHON_ENTITY_CACHE_LIMIT` | Forwarder / Setup | Telethon entity cache; minimum `100` | `500` |
| `INITIAL_SCAN_LIMIT` | Forwarder | First-scan message count per source; `0` skips history | `100` |
| `MIN_VOICE_DURATION_SECONDS` | Forwarder | Minimum first-message duration; `0` disables it | `0` |
| `MIN_VOICE_DURATION_EXEMPT_AUTHORS` | Forwarder | User IDs or `@usernames` exempt from the duration limit | empty |
| `INCLUDE_VIDEO_NOTES` | Forwarder | Treat round video messages as voice | `false` |
| `TELEGRAM_GENERAL_NOTIFICATION_CHAT` | Notifications | Separate general-notification target | — |
| `CODEX_APP_SERVER_EXECUTABLE` | Notifications | Absolute Codex executable; takes precedence over command | — |
| `CODEX_APP_SERVER_COMMAND` | Notifications | App-server command | `codex app-server` |
| `CODEX_RATE_LIMIT_ID` | Notifications | Codex rate-limit identifier | `codex` |
| `CODEX_RATE_LIMIT_POLL_SECONDS` | Notifications | Usage polling interval | `1800` |
| `CODEX_NOTIFICATION_STATE` | Notifications | Per-account monitor state | `data/codex-rate-limit.json` |
| `TELEGRAM_NOTIFICATION_LOG` | Notifications | Notification log | `data/logs/telegram-notifications.log` |

Treat the bot token and Telegram session as passwords. Do not commit `.env` or
runtime data.

## Execution

```powershell
# Voice forwarding
tg-forwarder run

# Reset all state, or only a time period/source
tg-forwarder reset
tg-forwarder reset=1W --source=-1001234567890

# General Telegram messages
tg-notification send-test
tg-notification send "Eigene Benachrichtigung"

# Codex/Work 5-hour and weekly usage monitor
tg-notification run
```

The usage monitor saves each active local Codex login as a private profile in
`data/codex-accounts/<account_id>/`, then starts a fresh local `codex app-server`
for every saved profile per poll. It sends reminders five minutes before, and
at, each predicted reset for used 5-hour and weekly windows of every account.
To add another account, sign in to it in Codex; the next monitor poll stores
its profile. Later polls continue to query every saved account profile. Before
the first start after upgrading, assign a pre-existing ungrouped state to the
currently signed-in account (a backup is created automatically):

```powershell
python scripts/migrate_codex_notification_accounts.py
```

### Continuous operation

#### Windows

Before installing the `tg-forwarder` service, complete its interactive
Telegram login with `tg-setup list-chats`. Install the automatic Windows
service with `shawl.exe` available in `tools`, on `PATH`, or through
`SHAWL_EXE`:

```powershell
.\scripts\install-windows-service.bat
```

The script requests administrator rights; if it cannot elevate the process,
install `gsudo` with `winget install gerardog.gsudo`. Remove the service with:

```powershell
.\scripts\uninstall-windows-service.bat
```

For `tg-notification`, install the per-user notification task (uses
`pythonw.exe` and no console window):

```powershell
.\scripts\Install-Windows-TgNotificationsTask.ps1 -StartNow
```

On Raspberry Pi OS, install the forwarder service after configuring `.env`:

```bash
bash scripts/install-raspberry-pi-service.sh
```

See [docs/architecture.md](docs/architecture.md) for technical architecture and
runtime behavior.
