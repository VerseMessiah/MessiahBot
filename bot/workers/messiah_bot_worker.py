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

        # categories (Discord type 4)
        cats = [c for c in chans if c.get("type") == 4]
        categories_payload = []
        for c in cats:
            cat_id = str(c["id"])

            # Pull ALL children for this category
            children = [ch for ch in chans if str(ch.get("parent_id")) == cat_id]

            # Split them but DO NOT overwrite the global lists
            text_like = [ch for ch in children if ch["type"] in (0, 5, 15)]
            voice_like = [ch for ch in children if ch["type"] in (2, 13)]

            # Sort each group by their real Discord position
            text_like.sort(key=lambda ch: ch["position"])
            voice_like.sort(key=lambda ch: ch["position"])

            # Convert to unified format
            text_sub = []
            for ch in text_like:
                if ch["type"] == 0:
                    subtype = "text"
                    raw = 0
                elif ch["type"] == 5:
                    subtype = "announcement"
                    raw = 5
                elif ch ["type"] == 15:
                    subtype = "forum"
                    raw = 15
                else:
                    subtype = "text"
                    raw = ch["type"]
                
                text_sub.append({
                    "name": ch["name"],
                    "type": subtype,
                    "raw_type": raw,
                    "topic": ch["topic"],
                    "position": ch["position"],
                    "options": {}
                })

            voice_sub = []
            for ch in voice_like:
                if ch["type"] == 2:
                    subtype = "voice"
                    raw = 2
                elif ch["type"] == 13:
                    subtype = "stage"
                    raw = 13
                else:
                    subtype = "voice"
                    raw = ch["type"]
                
                voice_sub.append({
                    "name": ch["name"],
                    "type": subtype,
                    "raw_type": raw,
                    "position": ch["position"],
                    "options": {}
                })

            # Merge text + voice into a single channels list for ServerBuilder compatibility
            combined = sorted(
                (text_sub + voice_sub),
                key=lambda ch: ch.get("position", 0)
            )

            categories_payload.append({
                "name": c["name"],
                "position": c["position"],
                # Used by ServerBuilder (single merged list)
                "channels": combined
            })

        categories_payload.sort(key=lambda x: x["position"])

        return {
            "mode": "update",
            "roles": roles_payload,
            "categories": categories_payload,
            "channels": [] 
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
#   HELPER: NORMALIZE LAYOUT
# ------------------------------------------------------------

def normalize_layout(layout: Dict[str, Any]) -> Dict[str, Any]:
    cats = layout.get("categories") or []
    if isinstance(cats, list):
        for cat in cats:
            if not isinstance(cat, dict):
                continue
            existing_channels = cat.get("channels") or []
            if existing_channels:
                continue
            text_sub = cat.get("channels_text") or []
            voice_sub = cat.get("channels_voice") or []
            merged = []
            for ch in list(text_sub) + list(voice_sub):
                if isinstance(ch, dict):
                    merged.append(ch)
            if merged:
                merged.sort(key=lambda ch: ch.get("position", 0))
                cat["channels"] = merged
    return layout

# ------------------------------------------------------------
#   HELPER: STORE LAYOUT VERSION
# ------------------------------------------------------------

def _store_layout_version(guild_id: str, layout: Dict[str, Any]) -> Dict[str, Any]:
    """
    Helper to insert a new layout row into builder_layouts and return metadata.
    Assumes DATABASE_URL is set and psycopg is available.
    """
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured on worker")

    # Ensure mode is set
    if not layout.get("mode"):
        layout["mode"] = "update"

    try:
        with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # Next version number for this guild
                cur.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS v FROM builder_layouts WHERE guild_id=%s",
                    (guild_id,),
                )
                ver = int((cur.fetchone() or {}).get("v", 1))

                # Insert full layout as JSONB payload
                cur.execute(
                    "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s,%s,%s::jsonb)",
                    (guild_id, ver, json.dumps(layout)),
                )
        return {"version": ver}
    except Exception as e:
        raise

# ------------------------------------------------------------
#   ROUTE: SAVE LAYOUT
# ------------------------------------------------------------

@app.post("/api/save_layout")
def api_save_layout():
    """
    Save a layout coming from the dashboard into builder_layouts
    so that /snapshot_layout, /build_server and /update_server
    all work off the same table.

    Expected JSON:
    {
      "guild_id": "1234567890",
      "layout": { ... full layout object ... }
    }
    """
    payload = request.json or {}
    gid = str(payload.get("guild_id", "")).strip()
    layout = payload.get("layout")

    if not gid or not isinstance(layout, dict):
        return jsonify({"ok": False, "error": "Missing or invalid guild_id/layout"}), 400

    # 🔁 Normalize categories for ServerBuilder:
    layout = normalize_layout(layout)

    try:
        meta = _store_layout_version(gid, layout)
        return jsonify({"ok": True, "version": meta["version"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------------------------------------------
#   ROUTE: SNAPSHOT LAYOUT (alias of save_layout for now)
# ------------------------------------------------------------

@app.post("/api/snapshot_layout")
def api_snapshot_layout():
    """
    Wrapper for dashboard snapshot button.
    Behaves the same as /api/save_layout for now, but kept separate
    so we can give it different semantics later (e.g. snapshot vs active).
    """
    payload = request.json or {}
    gid = str(payload.get("guild_id", "")).strip()
    layout = payload.get("layout")

    if not gid or not isinstance(layout, dict):
        return jsonify({"ok": False, "error": "Missing or invalid guild_id/layout"}), 400

    layout = normalize_layout(layout)

    try:
        meta = _store_layout_version(gid, layout)
        return jsonify({"ok": True, "version": meta["version"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------------------------------------------
#   ROUTE: BUILD SERVER
# ------------------------------------------------------------

@app.post("/api/build_server/<guild_id>")
def api_build_server(guild_id):
    """
    Dashboard hook for 'Build Server' if/when we want it.
    For now, it just stores the provided layout as a new version
    so the Discord slash command /build_server can consume the latest layout.
    """
    payload = request.json or {}
    layout = payload.get("layout")

    if not isinstance(layout, dict):
        return jsonify({"ok": False, "error": "Missing or invalid layout"}), 400

    # Normalize as with save_layout/snapshot_layout
    layout = normalize_layout(layout)

    try:
        meta = _store_layout_version(str(guild_id), layout)
        return jsonify({
            "ok": True,
            "version": meta["version"],
            "msg": "Layout stored. Run /build_server in Discord to apply it."
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------------------------------------------
#   ROUTE: UPDATE SERVER
# ------------------------------------------------------------

@app.post("/api/update_server/<guild_id>")
def api_update_server(guild_id):
    """
    Dashboard hook for 'Update Server'.
    Behaves like /api/build_server for now: stores a new version that
    /update_server (slash command) will pull as the latest layout.
    """
    payload = request.json or {}
    layout = payload.get("layout")

    if not isinstance(layout, dict):
        return jsonify({"ok": False, "error": "Missing or invalid layout"}), 400

    layout = normalize_layout(layout)

    try:
        meta = _store_layout_version(str(guild_id), layout)
        return jsonify({
            "ok": True,
            "version": meta["version"],
            "msg": "Layout stored. Run /update_server in Discord to apply it."
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ------------------------------------------------------------
#   ENTRYPOINT
# ------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    print(f"🚀 Messiah Worker online on port {port}")
    app.run(host="0.0.0.0", port=port)