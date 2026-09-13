# Architecture

## Package boundaries

`tg_api` is the Telegram integration library. It owns both client families:

- `TelegramGateway` owns the Telethon user-account connection, account dialog
  resolution, live handlers, account-side media operations, and reset helpers.
- `TelegramBotGateway` owns semantic Bot API operations: target-chat access,
  relay lifecycle, message copy/edit/delete, general text notifications, and
  interactive bot setup.
- The Bot API HTTP implementation is private to `tg_api`; neither application
  package imports it. `TelegramGateway` uses `TelegramBotGateway`, never the
  private transport directly.

`tg_forwarder` contains forwarder-specific policy, SQLite state, reset planning,
and runtime CLI orchestration. `tg_notification` contains general notification
sinks and the Codex rate-limit monitor. `tg_setup` contains interactive account
login, dialog listing, and setup of the bot shared by both applications. The
application packages do not depend on each other's runtime code.

## Runtime flows

Voice forwarding logs into the Telegram account, initializes the bot relay,
resolves the Bot API target and source chats, catches up from SQLite cursors,
then registers new-message and edited-message handlers. Voice media stays on
Telegram servers: the account sends the existing media reference to the private
bot chat; the bot copies that received message to the target and removes the
temporary relay message.

The forwarder keeps source-specific collection blocks and tracked target message
IDs in SQLite. A reset plans remote target deletion before changing local state;
when remote deletion fails, local state remains untouched.

The general notification monitor imports the currently active file-backed Codex
login into an account-specific `CODEX_HOME`, retaining a newer profile token
when its `last_refresh` is newer. It launches a short-lived local Codex app
server for the currently signed-in account profile per poll, reads both
observed usage windows, persists them with a per-poll `last_polled_at`
timestamp under the respective account ID in one JSON state file,
and schedules pre-reset, predicted-reset, and confirmation checks. Monitor
instances for the other saved accounts are created as well, so their
notification timers keep running from the stored reset timestamps without
polling them. Unused
windows do not schedule reset work.

## Design constraints

- Domain policy in `tg_forwarder.core` must remain deterministic and independent
  of Telegram, SQLite, files, environment variables, and the event loop.
- `bootstrap.py` is the composition root for concrete gateways and use cases.
- Telegram media must remain server-side; do not add media downloads or uploads.
- Preserve Telegram UTF-16 entity offsets when captions change.
- Source messages with Telegram content protection must not be bypassed.
- `tests/test_architecture.py` enforces the forwarder package's dependency
  direction and call-cycle rules.
