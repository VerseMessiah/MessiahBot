# MessiahBot — Web Dashboard (Restored UI)

Drop this `web/` folder into your repo root and update your Render web start command to:
```
gunicorn "web.dashboard_messiah:app" --bind 0.0.0.0:$PORT
```
The UI calls your existing routes: `/layout-config`, `/api/live_layout/<guild_id>`, `/submit-server-layout`.
