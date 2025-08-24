import os, asyncio, hashlib, datetime as dt
from typing import Dict, Any, List, Optional, Tuple, Set
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

    async def _crosswalk_all(self, conn, gid: int) -> List[dict]:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM schedule_crosswalk WHERE guild_id=%s", (str(gid),))
            return await cur.fetchall()

    async def _crosswalk_upsert(self, conn, *, gid: int, tw_id: Optional[str], dc_id: Optional[str], source: str, content_hash: str, tw_updated: Optional[str], dc_updated: Optional[str]):
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO schedule_crosswalk (guild_id, twitch_event_id, discord_event_id, source, content_hash, twitch_updated_at, discord_updated_at, last_synced_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (guild_id, COALESCE(twitch_event_id,''), COALESCE(discord_event_id,''))
                DO UPDATE SET
                  source=EXCLUDED.source,
                  content_hash=EXCLUDED.content_hash,
                  twitch_updated_at=EXCLUDED.twitch_updated_at,
                  discord_updated_at=EXCLUDED.discord_updated_at,
                  last_synced_at=NOW()
                """,
                (str(gid), tw_id, dc_id, source, content_hash, tw_updated, dc_updated),
            )

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
            disc_by_id = { d["id"]: d for d in disc_norm }

            # Pull Twitch schedule
            from . import twitch_api_messiah as twmod
            tw = twmod.TwitchAPI(http, TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
            access_token = o["access_token"]
            segs = await tw.get_schedule_segments(o["broadcaster_id"], access_token)
            tw_norm = [_normalize_twitch_segment(sg) for sg in segs]
            tw_by_id = { t["id"]: t for t in tw_norm if t.get("id") }

            # Crosswalk
            xw = await self._crosswalk_all(conn, guild.id)
            by_dc = {(row.get("discord_event_id") or ""): row for row in xw}
            by_tw = {(row.get("twitch_event_id") or ""): row for row in xw}

            # 1) Resolve known pairs (in crosswalk) and sync by last-write-wins
            handled_dc: Set[str] = set()
            handled_tw: Set[str] = set()

            for row in xw:
                dc_id = (row.get("discord_event_id") or "")
                tw_id = (row.get("twitch_event_id") or "")

                dc = disc_by_id.get(dc_id) if dc_id else None
                twi = tw_by_id.get(tw_id) if tw_id else None

                if not dc and not twi:
                    continue  # dangling row; keep for now

                # Compute hashes
                dc_hash = _hash_payload(dc) if dc else None
                tw_hash = _hash_payload(twi) if twi else None

                # Decide direction
                dc_up = _dt(dc["updated_at"]) if dc else None
                tw_up = _dt(twi["updated_at"]) if twi else None

                go = None
                if dc and twi:
                    if dc_hash != tw_hash:
                        # Prefer whichever appears newer
                        if dc_up and tw_up:
                            go = "dc_to_tw" if dc_up >= tw_up else "tw_to_dc"
                        elif dc_up:
                            go = "dc_to_tw"
                        elif tw_up:
                            go = "tw_to_dc"
                        else:
                            go = "dc_to_tw"  # default
                elif dc and not twi:
                    go = "dc_create_tw"
                elif twi and not dc:
                    go = "tw_create_dc"

                # Apply
                new_dc_id = dc_id
                new_tw_id = tw_id

                try:
                    if go == "dc_to_tw":
                        # push Discord → Twitch update
                        await self._upsert_twitch_from_dc(tw, o, access_token, twi, dc)
                        handled_dc.add(dc["id"]); handled_tw.add(twi["id"])
                    elif go == "tw_to_dc":
                        # push Twitch → Discord update
                        await self._upsert_discord_from_tw(guild, twi, dc, default_channel_id)
                        handled_dc.add(dc["id"]); handled_tw.add(twi["id"])
                    elif go == "dc_create_tw":
                        created_id = await self._create_tw_from_dc(tw, o, access_token, dc)
                        new_tw_id = created_id
                        handled_dc.add(dc["id"])
                    elif go == "tw_create_dc":
                        ev = await _discord_create_event(guild, twi, default_channel_id)
                        new_dc_id = str(ev.id)
                        handled_tw.add(twi["id"])

                    # Update crosswalk row
                    new_hash = _hash_payload(dc or twi)
                    await self._crosswalk_upsert(
                        conn,
                        gid=guild.id,
                        tw_id=new_tw_id or (twi["id"] if twi else None),
                        dc_id=new_dc_id or (dc["id"] if dc else None),
                        source="both" if (dc and twi) else ("discord" if dc else "twitch"),
                        content_hash=new_hash,
                        tw_updated=(twi or {}).get("updated_at"),
                        dc_updated=(dc or {}).get("updated_at"),
                    )
                except Exception as e:
                    print(f"[ScheduleSync] pair sync error (dc:{dc_id} tw:{tw_id}): {e}")

            # 2) Orphans: Discord events not in crosswalk
            for dc in disc_norm:
                if dc["id"] in handled_dc: 
                    continue
                if dc["id"] in by_dc:
                    continue
                # Try time/title match with Twitch
                match = self._fuzzy_find(tw_norm, dc)
                try:
                    if match:
                        # create pair by updating twitch from dc or vice versa if materially different
                        await self._upsert_twitch_from_dc(tw, o, access_token, match, dc)
                        await self._crosswalk_upsert(conn, gid=guild.id, tw_id=match["id"], dc_id=dc["id"],
                                                     source="both", content_hash=_hash_payload(dc),
                                                     tw_updated=match.get("updated_at"), dc_updated=dc.get("updated_at"))
                    else:
                        # create Twitch counterpart
                        new_tw_id = await self._create_tw_from_dc(tw, o, access_token, dc)
                        await self._crosswalk_upsert(conn, gid=guild.id, tw_id=new_tw_id, dc_id=dc["id"],
                                                     source="discord", content_hash=_hash_payload(dc),
                                                     tw_updated=None, dc_updated=dc.get("updated_at"))
                except Exception as e:
                    print(f"[ScheduleSync] orphan DC create error: {e}")

            # 3) Orphans: Twitch segments not in crosswalk
            for twi in tw_norm:
                tid = twi.get("id")
                if not tid or tid in handled_tw:
                    continue
                if tid in by_tw:
                    continue
                match = self._fuzzy_find(disc_norm, twi)
                try:
                    if match:
                        await self._upsert_discord_from_tw(guild, twi, match, default_channel_id)
                        await self._crosswalk_upsert(conn, gid=guild.id, tw_id=tid, dc_id=match["id"],
                                                     source="both", content_hash=_hash_payload(twi),
                                                     tw_updated=twi.get("updated_at"), dc_updated=match.get("updated_at"))
                    else:
                        ev = await _discord_create_event(guild, twi, default_channel_id)
                        await self._crosswalk_upsert(conn, gid=guild.id, tw_id=tid, dc_id=str(ev.id),
                                                     source="twitch", content_hash=_hash_payload(twi),
                                                     tw_updated=twi.get("updated_at"), dc_updated=None)
                except Exception as e:
                    print(f"[ScheduleSync] orphan TW create error: {e}")

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
        elif action in ("enable", "disable"):
            on = action == "enable"
            async with await self._db() as conn, conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO schedule_sync_settings (guild_id, enabled) VALUES (%s,%s) "
                    "ON CONFLICT (guild_id) DO UPDATE SET enabled=EXCLUDED.enabled",
                    (str(interaction.guild.id), on),
                )
            await interaction.response.send_message(f"🔁 Sync {'enabled' if on else 'disabled'}.", ephemeral=True)
        else:
            await interaction.response.send_message("Usage: /schedule_sync <now|enable|disable>", ephemeral=True)

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
