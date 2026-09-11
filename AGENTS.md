# AGENTS.md

## Project rules

Read [docs/architecture.md](docs/architecture.md) before changing Telegram
transport, forwarding, reset, or notification-monitor behavior.

- `tg_api` is the only package allowed to import Telethon or implement Telegram
  Bot API HTTP requests.
- `TelegramGateway` owns the Telethon account connection and uses only
  `TelegramBotGateway` for Bot API operations. The private Bot API transport
  must not be imported outside `tg_api`.
- `tg_forwarder` contains voice-forwarder policy and orchestration;
  `tg_notification` contains general-notification and Codex-monitor logic;
  `tg_setup` contains interactive provisioning. Keep these packages independent
  of each other's runtime code.
- Keep Telegram media server-side. Do not download or upload voice media unless
  a task explicitly requires it.
- Keep configuration validation in the respective `config.py`; document each
  new environment variable in `.env.example` and `README.md`.
- Keep processing state in `StateStore`. A remote deletion failure must not
  discard local state.
- Treat `data/codex-accounts/*` as private Codex homes. Never log, expose, or
  commit their `auth.json` files; update a profile from the active auth cache
  only when its `account_id` matches and its `last_refresh` is newer.
- Do not commit `.env`, Telegram session files, SQLite databases, credentials,
  phone numbers, or message contents.

## Validation

Run the affected tests during development and before completing a change run:

```powershell
python -m unittest discover -s tests -v
```

## Documentation

Keep `README.md` limited to requirements, setup, configuration, parameters, and
execution. Put architecture, internal behavior, historical context, and design
decisions in `docs/architecture.md`.
