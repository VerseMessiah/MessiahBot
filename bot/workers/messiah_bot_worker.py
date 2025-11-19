# bot/workers/messiah_worker.py

import os
import asyncio
import json
from typing import Dict, Any
import aiohttp
import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request
from flask_cors import CORS

from datetime import datetime as dt

# ------------------------------------------------------------
#   ENV
# ------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_API = "https://discord.com/api/v10"

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

# ------------------------------------------------------------
#   FLASK WORKER APP
# ------------------------------------------------------------

app = Flask(__name__)
CORS(app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=True)

# ------------------------------------------------------------
#   HELPER: Discord REST GET
# ------------------------------------------------------------

async def _dget(session, route: str):
    url = f"{DISCORD_API}{route}"
    async with session.get(url, headers={
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}"
    }) as r:
        if r.status == 429:
            data = await r.json()
            await asyncio.sleep(data.get("retry_after", 1))
            return await _dget(session, route)
        if r.status >= 400:
            text = await r.text()
            raise RuntimeError(f"Discord REST error {r.status}: {text}")
        return await r.json()

# ------------------------------------------------------------
#   SNAPSHOT HELPERS
# ------------------------------------------------------------

async def snapshot_guild(guild_id: str):
    """Pure REST-based snapshot of roles + categories + channels."""
    async with aiohttp.ClientSession() as http:
        # roles
        roles = await _dget(http, f"/guilds/{guild_id}/roles")
        roles_payload = []
        for r in roles:
            # Only exclude @everyone
            if r.get("name") == "@everyone":
                continue
            roles_payload.append({
                "name": r["name"],
                "color": f"#{int(r['color']):06x}",
                "position": r.get("position", 0),
                "perms": {
                    "admin": bool(int(r["permissions"]) & 0x8),
                    "manage_channels": bool(int(r["permissions"]) & 0x10),
                    "manage_roles": bool(int(r["permissions"]) & 0x20),
                    "view_channel": True,
                    "send_messages": True,
                    "connect": True,
                    "speak": True
                }
            })
        # Sort to match visual Discord UI (highest position first)
        roles_payload.sort(key=lambda x: x["position"], reverse=True)

        # channels
        chans = await _dget(http, f"/guilds/{guild_id}/channels")
        cats = [c for c in chans if c["type"] == 4]
        cats.sort(key=lambda c: c.get("position", 0))
        # Preserve API order for channels; do not pre-sort non-category channels
        non = [c for c in chans if c["type"] != 4]

        # Ensure categories and channels remain in true Discord UI order (top → bottom).
        # Categories: ascending position
        # Channels inside each category: ascending position

        categories_payload = []
        for c in cats:
            cat_id = str(c["id"])

            # Pull children first by true API order
            text_children = [
                ch for ch in non if c["type"] in [0, 5, 15]
                if str(ch.get("parent_id")) == cat_id 
            ]

            voice_children = [
                ch for ch in non if c["type"] in [2, 13]
                if str(ch.get("parent_id")) == cat_id
            ]

            # Hard‑enforce Discord UI rules:
            #  1) Text/Announcement/Forum in user-defined order (position asc)
            #  2) Voice exactly after all text/forum (position asc)
            #  3) Stage channels after voice (rare)
            text = [ch for ch in text_children if ch["type"] == 0]
            announcement = [ch for ch in text_children if ch["type"] == 5]
            forum = [ch for ch in text_children if ch["type"] == 15]
            stage = [ch for ch in voice_children if ch["type"] == 13]
            voice = [ch for ch in voice_children if ch["type"] == 2]

            text.sort(key=lambda ch: ch["position"])
            announcement.sort(key=lambda ch: ch["position"])
            forum.sort(key=lambda ch: ch["position"])
            voice.sort(key=lambda ch: ch["position"])
            stage.sort(key=lambda ch: ch["position"])

            text_ordered = text + announcement + forum
            voice_ordered = voice + stage

            text_sub = [
                {
                    "name": ch["name"],
                    "type": (
                        "text" if ch["type"] == 0 else
                        "forum" if ch["type"] == 15 else
                        "announcement" if ch["type"] == 5 else
                        "text"
                    ),
                    "raw_type": ch["type"],
                    "position": ch["position"],
                    "options": {}
                }
                for ch in text_ordered
            ]

            voice_sub = [
                {
                    "name": ch["name"],
                    "type": (
                        "voice" if ch["type"] == 2 else
                        "stage" if ch["type"] == 13 else
                        "voice"
                    ),
                    "raw_type": ch["type"],
                    "position": ch["position"],
                    "options": {}
                }
                for ch in voice_ordered
            ]

            categories_payload.append({
                "name": c["name"],
                "position": c["position"],
                "text channels": text_sub,
                "voice channels": voice_sub
            })

        categories_payload.sort(key=lambda x: x["position"])

        return {
            "mode": "update",
            "roles": roles_payload,
            "categories": categories_payload,
            "text channels": [],
            "voice channels": []
        }

# ------------------------------------------------------------
#   ROUTE: LIVE SNAPSHOT
# ------------------------------------------------------------

@app.get("/api/live_layout/<guild_id>")
def api_live_layout(guild_id):
    async def go():
        snap = await snapshot_guild(str(guild_id))
        return jsonify(snap)

    return asyncio.run(go())

# ------------------------------------------------------------
#   ROUTE: LATEST DB SNAPSHOT
# ------------------------------------------------------------

@app.get("/api/snapshot/<guild_id>")
def api_snapshot(guild_id):
    async def go():
        try:
            async with await psycopg.AsyncConnection.connect(
                DATABASE_URL, sslmode="require"
            ) as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        SELECT payload
                        FROM builder_layouts
                        WHERE guild_id=%s
                        ORDER BY version DESC
                        LIMIT 1
                    """, (str(guild_id),))
                    row = await cur.fetchone()
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        if not row:
            return jsonify({"ok": False, "error": "No snapshot found"}), 404

        return jsonify({"ok": True, "payload": row["payload"]})

    return asyncio.run(go())

# ------------------------------------------------------------
#   ROUTE: BASIC HEALTH CHECK
# ------------------------------------------------------------

@app.get("/api/ping")
def ping():
    return jsonify({"ok": True, "worker": "messiah_worker", "time": dt.utcnow().isoformat()})

# ------------------------------------------------------------
#   ENTRYPOINT
# ------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print(f"🚀 Messiah Worker online on port {port}")
    app.run(host="0.0.0.0", port=port)