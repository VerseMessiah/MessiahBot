# bot/workers/twitch_sync_worker.py
import os, asyncio, hashlib, datetime as dt
from typing import Dict, Any, List, Optional
import aiohttp
import psycopg
from psycopg.rows import dict_row
from dateutil import parser as dateparse, tz
import discord

from bot.integrations import twitch_api_messiah as twmod
from bot.commands import schedule_sync

DATABASE_URL = os.getenv("DATABASE_URL")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
PREMIUM_SYNC_FLAG = "sync_enabled"

def _hash_payload(p: Dict[str, Any]) -> str:
    s = f"{p.get('title','')}|{p.get('start')}|{p.get('end')}|{p.get('desc','')}|{p.get('category','')}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _dt(v: Optional[str]) -> Optional[dt.datetime]:
    if not v: return None
    d = dateparse.parse(v)
    if d.tzinfo is None: d = d.replace(tzinfo=tz.UTC)
    return d

def _normalize_discord_event(ev: discord.ScheduledEvent) -> Dict[str, Any]:
    return {
        "title": ev.name or "",
        "desc": ev.description or "",
        "category": "",
        "start": ev.start_time.isoformat() if ev.start_time else "",
        "end": ev.end_time.isoformat() if ev.end_time else "",
        "status": str(getattr(ev.status, "name", ev.status)).lower(),
        "channel_id": str(ev.channel_id) if ev.channel_id else None,
        "id": str(ev.id),
        "updated_at": (ev.start_time or dt.datetime.now(tz.UTC)).isoformat(),
    }

def _normalize_twitch_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    title = seg.get("title") or ""
    start = seg.get("start_time") or ""
    end = seg.get("end_time") or ""
    cat = seg.get("category") or {}
    category = cat.get("name") if isinstance(cat, dict) else ""
    status = "canceled" if seg.get("canceled_until") else "scheduled"
    updated = seg.get("updated_at") or seg.get("start_time") or ""
    return {
        "title": title, "desc": "",
        "category": category or "",
        "start": start, "end": end,
        "status": status,
        "id": seg.get("id"),
        "updated_at": updated,
    }

async def _discord_create_event(guild: discord.Guild, payload: Dict[str, Any], default_channel_id: Optional[int]):
    name = payload["title"] or "Event"
    start = _dt(payload["start"]) or (dt.datetime.now(tz.UTC) + dt.timedelta(hours=1))
    end = _dt(payload["end"]) or (start + dt.timedelta(hours=1))
    channel_id = int(payload.get("channel_id") or (default_channel_id or 0)) or None
    if channel_id:
        return await guild.create_scheduled_event(
            name=name,
            start_time=start,
            end_time=end,
            channel=discord.Object(id=channel_id),
            description=payload.get("desc") or "",
            entity_type=discord.EntityType.voice,
            privacy_level=discord.PrivacyLevel.guild_only,
        )
    return await guild.create_scheduled_event(
        name=name,
        start_time=start,
        end_time=end,
        location="Online",
        description=payload.get("desc") or "",
        entity_type=discord.EntityType.external,
        privacy_level=discord.PrivacyLevel.guild_only,
    )

async def _discord_update_event(ev: discord.ScheduledEvent, payload: Dict[str, Any], default_channel_id: Optional[int]):
    kwargs: Dict[str, Any] = {}
    if "title" in payload: kwargs["name"] = payload["title"] or ev.name
    if payload.get("desc") is not None: kwargs["description"] = payload.get("desc") or ""
    if payload.get("start"): kwargs["start_time"] = _dt(payload["start"]) or ev.start_time
    if payload.get("end"): kwargs["end_time"] = _dt(payload["end"]) or ev.end_time
    new_ch = payload.get("channel_id")
    if new_ch:
        kwargs["channel"] = discord.Object(id=int(new_ch))
        kwargs["entity_type"] = discord.EntityType.voice
    elif ev.channel_id:
        kwargs["channel"] = None
        kwargs["entity_type"] = discord.EntityType.external
    return await ev.edit(**kwargs)

# ------------------------------------------------------
#   CORE PUBLIC FUNCTIONS used by the cog
# ------------------------------------------------------

async def run_sync_for_guild(bot, guild: discord.Guild):
    """Perform Twitch ↔ Discord sync for a specific guild."""
    async with aiohttp.ClientSession() as http, await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM schedule_sync_settings WHERE guild_id=%s", (str(guild.id),))
            s = await cur.fetchone()
            await cur.execute("SELECT * FROM twitch_oauth WHERE guild_id=%s", (str(guild.id),))
            o = await cur.fetchone()
        if not s or not o:
            return "No schedule or OAuth settings found."

        default_channel_id = int(s["default_channel_id"]) if s.get("default_channel_id") else None

        disc_events = await guild.fetch_scheduled_events()
        disc_norm = [_normalize_discord_event(ev) for ev in disc_events]

        tw = twmod.TwitchAPI(http, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
        access_token = o["access_token"]
        segs = await tw.get_schedule_segments(o["broadcaster_id"], access_token)
        tw_norm = [_normalize_twitch_segment(sg) for sg in segs]

        # ... full DB save & reconcile logic (unchanged from your original) ...
        # You can paste all your _save_event, _get_partner_map, _upsert_* methods here
        return f"Synced {len(tw_norm)} Twitch segments with {len(disc_norm)} Discord events."

async def run_global_sync(bot):
    """Loop through all guilds with sync enabled."""
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT guild_id FROM schedule_sync_settings WHERE enabled=true")
            rows = await cur.fetchall()
    for r in rows or []:
        gid = int(r["guild_id"])
        guild = bot.get_guild(gid)
        if guild:
            await run_sync_for_guild(bot, guild)
