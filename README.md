# Telegram Voice Forwarder and Notifications

This tool monitors one or more Telegram chats with your personal Telegram
account and publishes new voice messages to a private channel through a bot.
Audio files are never downloaded locally.

The repository also contains an independent `telegram_notifications` package
for general Telegram notifications. It does not import or share runtime code
with the voice-message forwarder.

## Requirements

- Python 3.13 or newer
- Your account must be a member of the source chats.
- A Telegram bot must be an administrator in the target channel with permission
  to post messages. Your personal account does not need target-channel access.
- Consent from the affected group members or another appropriate legal basis
  for forwarding their messages

## Setup

1. Open [my.telegram.org/apps](https://my.telegram.org/apps), register an
   application, and note its `api_id` and `api_hash`.
2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .
   ```

3. Copy `.env.example` to `.env` and enter your credentials:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Sign in and list your available Telegram dialog IDs:

   ```powershell
   python -m telegram_voice_forwarder list-chats
   ```

   On the first run, Telegram asks for your phone number, login code, and, if
   enabled, your two-factor authentication password. The resulting session is
   stored locally at `TELEGRAM_SESSION`. Treat the generated `.session` file
   like a password.

5. Add the source and target IDs to `.env`:

   ```dotenv
   TELEGRAM_SOURCE_CHATS=-1001234567890,@another_group
   TELEGRAM_TARGET_CHAT=-1009876543210
   ```

6. Configure the publisher bot:

   - Open [@BotFather](https://t.me/BotFather) in Telegram and make sure it is
     the verified bot.
   - Send `/newbot`.
   - Enter a display name and then a unique username ending in `bot`.
   - Copy the HTTP API token returned by BotFather. Treat it like a password.
   - Add the bot to `TELEGRAM_TARGET_CHAT` as an administrator with permission
     to post and edit messages.
   - Run the setup command and paste the token when prompted:

   ```powershell
   python -m telegram_voice_forwarder setup-notification-bot
   ```

   Follow the displayed Start link so the account can use the private bot chat
   as a short-lived relay. The command validates target access and stores the
   token in `.env`.

7. Start monitoring:

   ```powershell
   python -m telegram_voice_forwarder run
   ```

## General Telegram notifications

General notifications use the same Bot API token but a separate target chat.
Configure `TELEGRAM_GENERAL_NOTIFICATION_CHAT` in `.env`; leave
`TELEGRAM_TARGET_CHAT` reserved for forwarded voice messages. The bot must be
allowed to send messages in the general target chat. For a private chat, open
the bot chat and press Start; for a group or channel, add the bot with send
permission.

Send a test message with the independent notification package:

```powershell
python -m telegram_notifications send-test
```

The Codex/Work rate-limit monitor runs as a separate process:

```powershell
python -m telegram_notifications run
```

For every polling interval, it starts a fresh local `codex app-server`, reads
the authenticated Codex rate limits, and immediately stops that process again.
It observes the `codex` primary bucket with a 300-minute window. A Telegram
message is sent five minutes before the predicted 5-hour reset and again at
the predicted reset time. A one-time confirmation poll runs 15 seconds after
that reset, then the regular interval resumes. The
monitor stores its last observation in
`CODEX_NOTIFICATION_STATE` so restarting it does not create a false initial
notification. `CODEX_RATE_LIMIT_POLL_SECONDS` defaults to 1,800 seconds
(30 minutes). If the 5-hour window is still completely unused, no reset timer
or confirmation poll is scheduled.

The monitor appends runtime messages to
`data/logs/telegram-notifications.log`. Override this location with
`TELEGRAM_NOTIFICATION_LOG` when needed; no log rotation is applied.
At startup, it writes the current primary and secondary usage windows (normally
the 5-hour and weekly limits) to the console.

The app-server must be available on the machine running this command and must
be signed in to the ChatGPT account whose Codex/Work usage should be monitored.
If the executable is not available as `codex` on `PATH`, set
`CODEX_APP_SERVER_EXECUTABLE` to its literal absolute path. This avoids shell
quoting issues on Windows, for example:

```dotenv
CODEX_APP_SERVER_EXECUTABLE=D:\Tools\01_portable\OpenAI.Codex_Microsoft.Winget.Source_8wekyb3d8bbwe\codex-x86_64-pc-windows-msvc.exe
```

`CODEX_APP_SERVER_EXECUTABLE` takes precedence over the advanced
`CODEX_APP_SERVER_COMMAND` setting.

The `send` command is available for future general notification sources:

### Windows autostart

The notification monitor needs the current user's Codex login. Therefore, on
Windows it is installed as a per-user Task Scheduler task rather than a system
service. It starts at logon, has no execution time limit, and restarts after a
failure. It uses `pythonw.exe`, so no console window is shown. Run the
installer from PowerShell 7.6 or newer:

```powershell
.\scripts\Install-Windows-TgNotificationsTask.ps1 -StartNow
```

The task is named `TelegramNotifications`. Re-running the installer safely
updates its configuration. With `-StartNow`, it also stops an already running
instance before starting the updated monitor.

```powershell
python -m telegram_notifications send "Eine eigene Benachrichtigung"
```

Run commands from the directory containing `.env`. If `TELEGRAM_SESSION` or
`STATE_DB` is relative and `.env` was loaded from another directory, the tool
stops with a configuration error to prevent using the wrong runtime data.

The API account sends each accepted media reference to its private bot chat.
The bot copies it server-side to the target and immediately deletes the relay
message. The audio is not downloaded or uploaded by this tool, although the
relay message can be visible very briefly in the private bot chat.

Configure the voice-duration threshold in seconds with
`MIN_VOICE_DURATION_SECONDS`. Use a decimal point for fractional values; `0`
disables the threshold:

```dotenv
MIN_VOICE_DURATION_SECONDS=3.5
```

Authors who are exempt from this threshold are configured with
`MIN_VOICE_DURATION_EXEMPT_AUTHORS` as a comma-separated list of numeric
Telegram user IDs or `@usernames`. Every voice message from these authors is
forwarded regardless of the threshold; collection blocks form unchanged for
them:

```dotenv
MIN_VOICE_DURATION_EXEMPT_AUTHORS=123456789,@alice
```

Telegram IDs for supergroups and channels usually start with `-100`. Configure
private groups without a public username by using their numeric ID.

Telethon keeps recently encountered users, chats, and channels in memory. This
project reduces Telethon's default cache limit from 5,000 to 500 entities. You
can adjust it in `.env`, but values below 100 are rejected because an
undersized cache can cause excessive session-database writes:

```dotenv
TELETHON_ENTITY_CACHE_LIMIT=500
```

## Restart behavior

SQLite stores the processing state, so a restart continues from the last
processed message. Only the first scan uses `INITIAL_SCAN_LIMIT`; set it to `0`
to skip initial history.

Reset all known messages, or only a recent period, with:

```powershell
python -m telegram_voice_forwarder reset
python -m telegram_voice_forwarder reset=1W
python -m telegram_voice_forwarder reset --source=-1001234567890
python -m telegram_voice_forwarder reset=1W --source=-1001234567890
```

Periods accept `H`, `D`, or `W`. A time-limited reset uses original Telegram
timestamps. Both reset modes remove known history and safely tracked target
messages before changing local state; older unmatched target messages are
reported. Stop the monitor before running a reset. Use `--source=CHAT` with a
numeric ID or username to limit either reset mode to one source chat; state and
target messages belonging to other sources remain untouched.

## Architecture

Business rules live in the dependency-free `core.py`; Telegram and SQLite are
adapters behind contracts from `ports.py`. `bootstrap.py` wires the application
together, while `cli.py` only handles commands. Architecture tests enforce the
one-way dependency and call hierarchy.

## Running continuously

Keep `.env`, the Telegram session, and SQLite state persistent and protected.

### Raspberry Pi OS service

Configure `.env`, then install and start the systemd service:

```bash
bash scripts/install-raspberry-pi-service.sh
```

Deployments mit `scripts/deploy-raspi.ps1` schreiben den Commit des sauberen
getrackten Arbeitsbaums nach `.source-revision`. Diese nicht geheime Datei wird
vom Raspberry-Pi-Konfigurationssnapshot als Quellrevision des installierten
Dienstes inventarisiert. Bei getrackten, nicht committeten Änderungen bricht
das Deployment ab, weil der Commit den übertragenen Quellstand sonst nicht
eindeutig beschreiben würde. Die lokale `.env` ist von Git ignoriert und wird
deshalb separat übertragen und auf dem Pi mit `600` geschützt; fehlt sie,
bricht das Deployment ebenfalls ab.

The installer adds missing APT dependencies, requires Python 3.13+, creates the
virtual environment, performs an interactive Telegram login when needed, and
enables automatic startup. It uses the current user, or `SUDO_USER` when run
through `sudo`; override this with `SERVICE_USER=pi`.

```bash
sudo systemctl status telegram-voice-forwarder.service
sudo journalctl -u telegram-voice-forwarder.service -f
```

### Windows service

The Windows installer uses [Shawl](https://github.com/mtkennerly/shawl). Before
running it:

1. Complete setup and the interactive Telegram login.
2. Put `shawl.exe` in `tools\`, `PATH`, or `SHAWL_EXE`.
3. From a non-elevated terminal, install
   [gsudo](https://gerardog.github.io/gsudo/docs/install):

   ```powershell
   winget install gerardog.gsudo
   ```

Install or uninstall the service from the project directory:

```powershell
.\scripts\install-windows-service.bat
.\scripts\uninstall-windows-service.bat
```

The service starts automatically and restarts after failures. It runs as
`LocalSystem` by default; select another account in `services.msc` if needed.

```powershell
sc.exe query TelegramVoiceForwarder
sc.exe stop TelegramVoiceForwarder
sc.exe start TelegramVoiceForwarder
```

Application and Shawl logs are stored in `data\logs\service_rCURRENT.log` and
`data\logs\shawl_rCURRENT.log`. Uninstalling preserves all project and runtime
data.

## Security and Telegram rules

The session grants access to your Telegram account. Never commit or share it,
and never include it in a public container image. Telegram also notes that
third-party clients are monitored and that abuse such as spam can result in an
account ban. Only use this tool in groups where you are allowed to transfer the
messages.
