## Forwarder setup

1. Open [my.telegram.org/apps](https://my.telegram.org/apps), register an
   application, and note its `api_id` and `api_hash`.
2. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -e .
   ```

3. Copy `.env.example` to `.env` and enter the Telegram application
   credentials:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Sign in and list the available Telegram dialog IDs:

   ```powershell
   tg-setup list-chats
   ```

   On the first run, Telegram asks for the phone number, login code, and, if
   enabled, the two-factor authentication password. The resulting session is
   stored at `TELEGRAM_SESSION`. Treat the generated `.session` file like a
   password.

5. Add the source and target IDs to `.env`:

   ```dotenv
   TELEGRAM_SOURCE_CHATS=-1001234567890,@another_group
   TELEGRAM_TARGET_CHAT=-1009876543210
   ```

6. Configure the publisher bot:

   - Open [@BotFather](https://t.me/BotFather) in Telegram and verify that it
     is the official bot.
   - Send `/newbot` and choose a display name and a unique username ending in
     `bot`.
   - Copy the HTTP API token returned by BotFather. Treat it like a password.
   - Add the bot to `TELEGRAM_TARGET_CHAT` as an administrator with permission
     to post and edit messages.
   - Run the setup command and paste the token when prompted:

   ```powershell
   tg-setup configure-bot
   ```

   Follow the displayed Start link so the account can use the private bot chat
   as a short-lived relay. At least one of `TELEGRAM_TARGET_CHAT` or
   `TELEGRAM_GENERAL_NOTIFICATION_CHAT` must be configured. The command
   validates every configured target and stores the token in `.env`.

7. Start monitoring:

   ```powershell
   tg-forwarder run
   ```

## Restart behavior

SQLite stores the forwarding state, so a restart continues after the last
processed message. Only the first scan uses `INITIAL_SCAN_LIMIT`; set it to
`0` to skip initial history.

Reset all known messages, or only a recent period, with:

```powershell
tg-forwarder reset
tg-forwarder reset=1W
tg-forwarder reset --source=-1001234567890
tg-forwarder reset=1W --source=-1001234567890
```

Periods accept `H`, `D`, or `W`. A time-limited reset uses original Telegram
timestamps. Both reset modes remove known history and safely tracked target
messages before changing local state; older unmatched target messages are
reported. Stop the monitor before running a reset. Use `--source=CHAT` with a
numeric ID or username to limit either reset mode to one source chat; state and
target messages belonging to other sources remain untouched.
