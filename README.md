# MessiahBot

Multi-service repo for the VerseMessiah ecosystem (Discord bot, Twitch sync, Flask dashboard).

## Services (Render Blueprint)
- `messiahbot-dashboard` / `messiahbot-dashboard-tst` — Flask dashboard
- `messiahbot-worker` / `messiahbot-worker-tst` — Discord bot
- `messiahbot-twitch-sync` / `messiahbot-twitch-sync-tst` — schedule sync worker

## Quick Start (Local)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export FLASK_APP=web/dashboard_messiah.py
python web/dashboard_messiah.py  # dev only
python -m bot.messiahbot_dc      # bot
```

## Env
See `.env.example.env`. Use Render "Environment Group" named `messiahbot-core` and add service-specific overrides if needed.
