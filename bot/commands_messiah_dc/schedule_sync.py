import os, asyncio, hashlib, datetime as dt
from typing import Dict, Any, List, Optional
import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import psycopg
from psycopg.rows import dict_row
from dateutil import parser as dateparse, tz

from .server_builder import _norm  # reuse normalizer

DATABASE_URL = os.getenv("DATABASE_URL")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

PREMIUM_SYNC_FLAG = "sync_enabled"  # boolean column on schedule_sync_settings

def _hash_payload(p: Dict[str, Any]) -> str:
    s = f"{p.get('title','')}|{p.get('start')}|{p.get('end')}|{p.get('desc','')}|{p.get('category','')}"
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _dt(v: Optional[str]) -> Optional[dt.datetime]:
    if not v: return None
    d = dateparse.parse(v)
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz.UTC)
    return d

def _normalize_discord_event(ev: discord.ScheduledEvent) -> Dict[str, Any]:
    return {
        "title": ev.name or "",
        "desc": ev.description or "",
        "category": "",  # optional mapping could embed category name at top of description
        "start": ev.start_time.isoformat() if ev.start_time else "",
        "end": ev.end_time.isoformat() if ev.end_time else "",
        "status": str(getattr(ev.status, "name", ev.status)).lower(),
        "channel_id": str(ev.channel_id) if ev.channel_id else None,
        "id": str(ev.id),
        "updated_at": (ev.start_time or dt.datetime.now(tz.UTC)).isoformat(),  # discord.py lacks explicit edited timestamp; use start as proxy
    }

def _normalize_twitch_segment(seg: Dict[str, Any]) -> Dict[str, Any]:
    title = seg.get("title") or ""
    start = seg.get("start_time") or ""
    # Some responses include end_time; otherwise compute via duration if needed
    end = seg.get("end_time") or ""
    cat = seg.get("category") or {}
    category = cat.get("name") if isinstance(cat, dict) else ""
    status = "canceled" if seg.get("canceled_until") else "scheduled"
    updated = seg.get("updated_at") or seg.get("start_time") or ""
    return {
        "title": title,
        "desc": "",
        "category": category or "",
        "start": start,
        "end": end,
        "status": status,
        "id": seg.get("id"),
        "updated_at": updated,
    }

async def _discord_create_event(guild: discord.Guild, payload: Dict[str, Any], default_channel_id: Optional[int]) -> discord.ScheduledEvent:
    name = payload["title"] or "Event"
    start = _dt(payload["start"]) or (dt.datetime.now(tz.UTC) + dt.timedelta(hours=1))
    end = _dt(payload["end"]) or (start + dt.timedelta(hours=1))
    channel_id = int(payload.get("channel_id") or (default_channel_id or 0)) or None

    # Discord requires a location or a channel. Use a channel if provided, else external location
    if channel_id:
        return await guild.create_scheduled_event(
            name=name,
            start_time=start,
            end_time=end,
            location=None,
            channel=discord.Object(id=channel_id),
            description=payload.get("desc") or "",
            entity_type=discord.EntityType.voice if channel_id else discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
        )
    else:
        return await guild.create_scheduled_event(
            name=name,
            start_time=start,
            end_time=end,
            location="Online",
            description=payload.get("desc") or "",
            entity_type=discord.EntityType.external,
            privacy_level=discord.PrivacyLevel.guild_only,
        )

async def _discord_update_event(ev: discord.ScheduledEvent, payload: Dict[str, Any], default_channel_id: Optional[int]) -> discord.ScheduledEvent:
    kwargs: Dict[str, Any] = {}
    if "title" in payload:
        kwargs["name"] = payload["title"] or ev.name
    if payload.get("desc") is not None:
        kwargs["description"] = payload.get("desc") or ""
    if payload.get("start"):
        kwargs["start_time"] = _dt(payload["start"]) or ev.start_time
    if payload.get("end"):
        kwargs["end_time"] = _dt(payload["end"]) or ev.end_time

    # channel/location
    new_ch = payload.get("channel_id")
    if new_ch:
        kwargs["channel"] = discord.Object(id=int(new_ch))
        kwargs["entity_type"] = discord.EntityType.voice
    elif ev.channel_id:
        # switch to external if removing channel
        kwargs["channel"] = None
        kwargs["entity_type"] = discord.EntityType.external

    return await ev.edit(**kwargs)

class ScheduleSync(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._loop = self.sync_loop
        self._loop.start()

    def cog_unload(self):
        self._loop.cancel()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        try:
            await self._sync_all_guilds()
        except Exception as e:
            print(f"[ScheduleSync] loop error: {e}")

    @sync_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    # -------- DB helpers --------
    async def _db(self):
        return await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require")

    async def _settings(self, conn, gid: int) -> Optional[dict]:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM schedule_sync_settings WHERE guild_id=%s", (str(gid),))
            return await cur.fetchone()

    async def _oauth(self, conn, gid: int) -> Optional[dict]:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM twitch_oauth WHERE guild_id=%s", (str(gid),))
            return await cur.fetchone()

    async def _user_premium(self, conn, gid: int) -> bool:
        """Premium gating: rely on schedule_sync_settings.sync_enabled (boolean)."""
        async with conn.cursor(row_factory=dict_row) as cur:
            try:
                await cur.execute("SELECT COALESCE(" + PREMIUM_SYNC_FLAG + ", false) AS on FROM schedule_sync_settings WHERE guild_id=%s", (str(gid),))
                row = await cur.fetchone()
                return bool(row and row.get("on"))
            except Exception:
                return False

    async def _save_event(self, conn, *, gid: int, source: str, payload: Dict[str, Any], partner_id: Optional[str] = None):
        """Upsert a normalized event into synced_events and mark last_seen=NOW().
        Expected table columns:
          guild_id (text), source (text), source_event_id (text), partner_event_id (text),
          title (text), description (text), category (text), start_at (timestamptz), end_at (timestamptz),
          status (text), updated_at (timestamptz), content_hash (text), last_seen (timestamptz)
        Primary/unique key: (guild_id, source, source_event_id)
        """
        src_id = str(payload.get("id") or "")
        content_hash = _hash_payload(payload)
        start_at = _dt(payload.get("start"))
        end_at = _dt(payload.get("end"))
        updated_at = _dt(payload.get("updated_at")) or start_at or dt.datetime.now(dt.timezone.utc)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO synced_events (guild_id, source, source_event_id, partner_event_id,
                                           title, description, category, start_at, end_at,
                                           status, updated_at, content_hash, last_seen)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (guild_id, source, source_event_id)
                DO UPDATE SET
                  partner_event_id = COALESCE(EXCLUDED.partner_event_id, synced_events.partner_event_id),
                  title = EXCLUDED.title,
                  description = EXCLUDED.description,
                  category = EXCLUDED.category,
                  start_at = EXCLUDED.start_at,
                  end_at = EXCLUDED.end_at,
                  status = EXCLUDED.status,
                  updated_at = EXCLUDED.updated_at,
                  content_hash = EXCLUDED.content_hash,
                  last_seen = NOW()
                """,
                (
                    str(gid), source, src_id, (str(partner_id) if partner_id else None),
                    payload.get("title") or "", payload.get("desc") or "", payload.get("category") or "",
                    start_at, end_at, payload.get("status") or "scheduled", updated_at, content_hash,
                ),
            )

    async def _get_partner_map(self, conn, gid: int) -> Dict[str, Dict[str, Optional[str]]]:
        """Return a mapping of source->source_event_id->partner_event_id for this guild."""
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT source, source_event_id, partner_event_id FROM synced_events WHERE guild_id=%s",
                (str(gid),),
            )
            rows = await cur.fetchall()
        out: Dict[str, Dict[str, Optional[str]]] = {"twitch": {}, "discord": {}}
        for r in rows or []:
            src = (r.get("source") or "").lower()
            if src in ("twitch", "discord"):
                out.setdefault(src, {})[str(r.get("source_event_id"))] = (r.get("partner_event_id") or None)
        return out

    # -------- main sync --------
    async def _sync_all_guilds(self):
        async with await self._db() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT guild_id FROM schedule_sync_settings WHERE enabled=true")
                rows = await cur.fetchall()
        for r in rows or []:
            gid = int(r["guild_id"])
            guild = self.bot.get_guild(gid)
            if guild:
                try:
                    await self._sync_one_guild(guild)
                except Exception as e:
                    print(f"[ScheduleSync] sync guild {gid} error: {e}")

    async def _sync_one_guild(self, guild: discord.Guild):
        async with aiohttp.ClientSession() as http, await self._db() as conn:
            s = await self._settings(conn, guild.id)
            o = await self._oauth(conn, guild.id)
            if not s or not o:
                return

            default_channel_id = int(s["default_channel_id"]) if s.get("default_channel_id") else None

            # Pull Discord events
            disc_events = await guild.fetch_scheduled_events()
            disc_norm = [_normalize_discord_event(ev) for ev in disc_events]
            # Pull Twitch schedule
            from . import twitch_api_messiah as twmod
            tw = twmod.TwitchAPI(http, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
            access_token = o["access_token"]
            segs = await tw.get_schedule_segments(o["broadcaster_id"], access_token)
            tw_norm = [_normalize_twitch_segment(sg) for sg in segs]

            # Persist a snapshot of current platform events
            for d in disc_norm:
                await self._save_event(conn, gid=guild.id, source="discord", payload=d)
            for t in tw_norm:
                await self._save_event(conn, gid=guild.id, source="twitch", payload=t)

            # If premium sync is not enabled, stop after saving
            premium_on = await self._user_premium(conn, guild.id)
            if not premium_on:
                return

            # Partner map used to avoid creating Twitch events from Discord-only items
            partner_map = await self._get_partner_map(conn, guild.id)

            # Twitch is primary: ensure each Twitch segment has a Discord counterpart; updates flow both ways only if a pair exists
            for twi in tw_norm:
                tw_id = twi.get("id")
                if not tw_id:
                    continue
                dc_partner_id = (partner_map.get("twitch", {}).get(str(tw_id)))

                if dc_partner_id:
                    # Update the existing Discord event from Twitch
                    try:
                        await self._upsert_discord_from_tw(guild, twi, {"id": str(dc_partner_id)}, default_channel_id)
                    except Exception as e:
                        print(f"[ScheduleSync] tw->dc update error for {tw_id}: {e}")
                    else:
                        await self._save_event(conn, gid=guild.id, source="twitch", payload=twi, partner_id=str(dc_partner_id))
                    continue

                # No known partner: try fuzzy match against current Discord events (don’t touch unrelated Discord-only events)
                match = self._fuzzy_find(disc_norm, twi)
                if match:
                    try:
                        await self._upsert_discord_from_tw(guild, twi, match, default_channel_id)
                        await self._save_event(conn, gid=guild.id, source="twitch", payload=twi, partner_id=str(match["id"]))
                    except Exception as e:
                        print(f"[ScheduleSync] tw orphan pair error: {e}")
                else:
                    # Create a new Discord event for this Twitch segment
                    try:
                        ev = await _discord_create_event(guild, twi, default_channel_id)
                        await self._save_event(conn, gid=guild.id, source="twitch", payload=twi, partner_id=str(ev.id))
                    except Exception as e:
                        print(f"[ScheduleSync] tw create dc error: {e}")

            # Discord updates should only flow to Twitch if a Twitch partner already exists (no creation from Discord-only events)
            from . import twitch_api_messiah as twmod
            tw = twmod.TwitchAPI(http, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
            for dc in disc_norm:
                dc_id = dc.get("id")
                if not dc_id:
                    continue
                tw_partner_id = (partner_map.get("discord", {}).get(str(dc_id)))
                if not tw_partner_id:
                    continue  # Discord-only event; leave it
                try:
                    await self._upsert_twitch_from_dc(tw, o, access_token, {"id": str(tw_partner_id)}, dc)
                    await self._save_event(conn, gid=guild.id, source="discord", payload=dc, partner_id=str(tw_partner_id))
                except Exception as e:
                    print(f"[ScheduleSync] dc->tw update error for {dc_id}: {e}")

    # ---- reconcile helpers ----
    def _fuzzy_find(self, pool: List[Dict[str, Any]], target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Shallow fuzzy match by title and start within 30 minutes."""
        t_title = _norm(target.get("title"))
        t_start = _dt(target.get("start"))
        if not t_start:
            return None
        for p in pool:
            p_title = _norm(p.get("title"))
            p_start = _dt(p.get("start"))
            if not p_start:
                continue
            if t_title == p_title and abs((t_start - p_start).total_seconds()) <= 1800:
                return p
        return None

    async def _upsert_twitch_from_dc(self, tw_api, oauth_row, access_token: str, existing_tw: Optional[Dict[str, Any]], dc: Dict[str, Any]):
        """Create or update Twitch to reflect a Discord item."""
        broadcaster_id = oauth_row["broadcaster_id"]
        title = dc["title"]
        start = _dt(dc["start"]) or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        end = _dt(dc["end"]) or (start + dt.timedelta(hours=1))
        duration_mins = int((end - start).total_seconds() // 60)
        category_id = None  # if you want to map Discord desc/category -> a Twitch category_id, add logic here

        if existing_tw and existing_tw.get("id"):
            await tw_api.update_segment(
                broadcaster_id, access_token, existing_tw["id"],
                title=title, start_time=start, duration_mins=duration_mins, category_id=category_id
            )
        else:
            await tw_api.create_segment(
                broadcaster_id, access_token,
                title=title, start_time=start, duration_mins=duration_mins, category_id=category_id
            )

    async def _create_tw_from_dc(self, tw_api, oauth_row, access_token: str, dc: Dict[str, Any]) -> str:
        broadcaster_id = oauth_row["broadcaster_id"]
        title = dc["title"]
        start = _dt(dc["start"]) or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)
        end = _dt(dc["end"]) or (start + dt.timedelta(hours=1))
        duration_mins = int((end - start).total_seconds() // 60)
        resp = await tw_api.create_segment(broadcaster_id, access_token, title=title, start_time=start, duration_mins=duration_mins)
        # Response contains the schedule with segments; extract the new segment id
        try:
            segs = (((resp or {}).get("data") or {}).get("segments") or [])
            return segs[-1]["id"]
        except Exception:
            # Fallback: no id? return empty and let next poll pair it by fuzzy match
            return ""

    async def _upsert_discord_from_tw(self, guild: discord.Guild, twi: Dict[str, Any], existing_dc: Optional[Dict[str, Any]], default_channel_id: Optional[int]):
        payload = {
            "title": twi["title"],
            "desc": (f"Game: {twi['category']}\n" if twi.get("category") else "") + "",
            "start": twi["start"],
            "end": twi["end"],
            "category": twi.get("category") or "",
        }
        if existing_dc and existing_dc.get("id"):
            ev = discord.utils.get(await guild.fetch_scheduled_events(), id=int(existing_dc["id"]))
            if ev:
                await _discord_update_event(ev, payload, default_channel_id)
        else:
            await _discord_create_event(guild, payload, default_channel_id)

    # -------- Commands --------
    @app_commands.command(name="schedule_sync", description="Messiah: control Twitch↔Discord schedule sync")
    @app_commands.describe(action="now | enable | disable")
    async def schedule_sync(self, interaction: discord.Interaction, action: str):
        if action == "now":
            await interaction.response.defer(ephemeral=True)
            try:
                await self._sync_one_guild(interaction.guild)
                await interaction.followup.send("✅ Schedules synced.", ephemeral=True)
            except Exception as e:
                await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)
        elif action in ("enable_sync", "disable_sync"):
            on = action == "enable_sync"
            async with await self._db() as conn, conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO schedule_sync_settings (guild_id, " + PREMIUM_SYNC_FLAG + ") VALUES (%s,%s) "
                    "ON CONFLICT (guild_id) DO UPDATE SET " + PREMIUM_SYNC_FLAG + "=EXCLUDED." + PREMIUM_SYNC_FLAG,
                    (str(interaction.guild.id), on),
                )
            await interaction.response.send_message(f"🔁 Premium sync {'enabled' if on else 'disabled' }.", ephemeral=True)
        else:
            await interaction.response.send_message("Usage: /schedule_sync <now|enable_sync|disable_sync>", ephemeral=True)

    @app_commands.command(name="schedule_sync_set_channel", description="Messiah: set default Discord channel for events")
    async def schedule_sync_set_channel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel):
        async with await self._db() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO schedule_sync_settings (guild_id, default_channel_id) VALUES (%s,%s) "
                "ON CONFLICT (guild_id) DO UPDATE SET default_channel_id=EXCLUDED.default_channel_id",
                (str(interaction.guild.id), str(channel.id)),
            )
        await interaction.response.send_message(f"📌 Default event channel set to {channel.mention}.", ephemeral=True)

    @app_commands.command(name="schedule_sync_set_tz", description="Messiah: set time zone for Twitch↔Discord sync")
    async def schedule_sync_set_tz(self, interaction: discord.Interaction, tz_name: str):
        async with await self._db() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO schedule_sync_settings (guild_id, tz) VALUES (%s,%s) "
                "ON CONFLICT (guild_id) DO UPDATE SET tz=EXCLUDED.tz",
                (str(interaction.guild.id), tz_name),
            )
        await interaction.response.send_message(f"🕒 Time zone set to `{tz_name}`.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ScheduleSync(bot))
