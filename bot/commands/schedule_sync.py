import asyncio
import datetime as dt
import time
import logging

import aiohttp
import discord
from discord import Guild, ScheduledEvent
from discord.ext import commands

from bot.integrations.db import fetch_one, execute
from bot.integrations.twitch_api import TwitchAPI

logger = logging.getLogger(__name__)


def normalize_twitch_segment(raw: dict) -> dict:
    category = raw.get("category") or {}
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Stream",
        "game": category.get("name") or "Unknown Game",
        "start_time": raw.get("start_time"),
        "end_time": raw.get("end_time"),
    }


def _parse_iso_z(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _segment_url(segment_id: str, login: str = "versemessiah") -> str:
    return f"https://twitch.tv/{login}?segment_id={segment_id}"


def _extract_segment_id(location: str | None) -> str | None:
    if not location or "segment_id=" not in location:
        return None
    return location.split("segment_id=", 1)[1].split("&", 1)[0].strip() or None

# Twitch rotates refresh tokens: a successful refresh immediately invalidates the
# old one. If we then fail to persist the new one, the stored token is dead and the
# only recovery is re-running OAuth — so retry the save, then fail loudly.
REFRESH_SAVE_ATTEMPTS = 3

# Only used if Twitch omits expires_in, which it normally does not.
FALLBACK_TOKEN_TTL_SECONDS = 3600


def _as_aware_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Normalize a Postgres timestamp to timezone-aware UTC.

    TIMESTAMPTZ comes back timezone-aware, but a plain TIMESTAMP comes back naive,
    and comparing naive to aware raises TypeError. Naive values are assumed UTC,
    so this works regardless of how the column was declared.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


async def get_valid_access_token(session: aiohttp.ClientSession, guild_id: str) -> tuple[str, str]:
    row = await fetch_one(
        """
        SELECT twitch_user_id, access_token, refresh_token, expires_at
        FROM twitch_tokens
        WHERE guild_id = %s
        ORDER BY updated_at DESC NULLS LAST
        LIMIT 1
        """,
        (guild_id,),
    )

    if not row:
        raise ValueError("❌ No Twitch connection found for this server")

    broadcaster_id = row["twitch_user_id"]
    access_token = row["access_token"]
    expires_at = _as_aware_utc(row["expires_at"])

    logger.info(f"Twitch token for guild {guild_id} expires_at={expires_at}")

    # Refresh within a 5-minute safety margin. A missing expiry counts as expired:
    # one wasted refresh beats a surprise 401 partway through a command.
    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    if expires_at is not None and expires_at > deadline:
        return broadcaster_id, access_token

    logger.info("Twitch token expired or unknown; refreshing…")
    api = TwitchAPI(session)
    new_token_data = await api.refresh_user_token(row["refresh_token"])

    # --- Past this point the OLD refresh token is dead. The new one MUST persist. ---
    access_token = new_token_data["access_token"]
    new_refresh_token = new_token_data["refresh_token"]

    # Trust the lifetime Twitch reports (~4h for user tokens) instead of assuming
    # 60 minutes; guessing low meant refreshing roughly 4x more often than needed.
    ttl_seconds = int(new_token_data.get("expires_in") or FALLBACK_TOKEN_TTL_SECONDS)

    last_error: Exception | None = None
    for attempt in range(1, REFRESH_SAVE_ATTEMPTS + 1):
        try:
            await execute(
                """
                UPDATE twitch_tokens
                SET access_token = %s,
                    refresh_token = %s,
                    expires_at = NOW() + (%s * INTERVAL '1 second'),
                    updated_at = NOW()
                WHERE guild_id = %s AND twitch_user_id = %s
                """,
                (access_token, new_refresh_token, ttl_seconds, guild_id, broadcaster_id),
            )
            logger.info(f"Saved refreshed Twitch token; valid for {ttl_seconds}s")
            return broadcaster_id, access_token
        except Exception as e:
            last_error = e
            logger.warning(
                f"Could not save refreshed Twitch token "
                f"(attempt {attempt}/{REFRESH_SAVE_ATTEMPTS}): {e}"
            )
            if attempt < REFRESH_SAVE_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)

    raise RuntimeError(
        "Refreshed the Twitch token but could not save it, so the stored refresh "
        "token is now invalid. Re-link Twitch from the dashboard to restore access. "
        f"Last database error: {last_error}"
    )

class ScheduleSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="debug_twitch")
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def debug_twitch(self, ctx):
        guild: Guild = ctx.guild

        # cache 60s to avoid Twitch 429 while testing
        now = time.time()
        cache = getattr(self.bot, "_twitch_schedule_cache", {})
        cached = cache.get(str(guild.id))
        if cached and (now - cached.get("ts", 0) < 60):
            await ctx.send(cached.get("msg", ""))
            return

        async with aiohttp.ClientSession() as session:
            try: 
                broadcaster_id, access_token = await get_valid_access_token(session, str(guild.id))
            except Exception as e:
                logger.error(f"Error getting token: {e}", exc_info=True)
                await ctx.send(f"❌ {e}")
                return
            api = TwitchAPI(session)
            try:
                # This command only ever prints the next 10, so one page is enough.
                raw_segments = await api.get_schedule_segments(
                    broadcaster_id, access_token, first=10, max_pages=1
                )
            except Exception as e:
                logger.error(f"Error fetching Twitch schedule: {e}", exc_info=True)
                await ctx.send(f"❌ Could not read the Twitch schedule: {e}")
                return

        segs = [normalize_twitch_segment(s) for s in (raw_segments or [])]
        if not segs:
            await ctx.send("ℹ️ Twitch schedule is empty.")
            return


        lines = []
        for s in segs:
            seg_id = s["id"] or ""
            lines.append(f"• {s['start_time']} → {s['end_time']} | {s['title']} | id: {seg_id[:18]}…")

        msg_out = "**Twitch schedule (next 10):**\n" + "\n".join(lines)
        cache[str(guild.id)] = {"ts": now, "msg": msg_out}
        self.bot._twitch_schedule_cache = cache
        await ctx.send(msg_out)

    @commands.command(name="twitch_import")
    @commands.guild_only()
    @commands.cooldown(1, 120, commands.BucketType.guild)
    async def twitch_import(self, ctx):
        guild: Guild = ctx.guild
        await ctx.send("⏳ Importing Twitch schedule into Discord…")

        async with aiohttp.ClientSession() as session:
            try: 
                broadcaster_id, access_token = await get_valid_access_token(session, str(guild.id))
            except Exception as e:
                logger.error(f"Error getting token: {e}", exc_info=True)
                await ctx.send(f"❌ {e}")
                return
            api = TwitchAPI(session)
            try:
                # 4 pages x 25 = up to 100 segments, which is far more schedule than
                # Discord events are useful for, and well inside the Helix budget.
                raw_segments = await api.get_schedule_segments(
                    broadcaster_id, access_token, first=25, max_pages=4
                )
            except Exception as e:
                logger.error(f"Error fetching Twitch schedule: {e}", exc_info=True)
                await ctx.send(f"❌ Could not read the Twitch schedule: {e}")
                return

        segments = [normalize_twitch_segment(s) for s in (raw_segments or [])]
        if not segments:
            await ctx.send("ℹ️ Twitch schedule is empty.")
            return

        # existing Discord events indexed by segment_id in location
        events = await guild.fetch_scheduled_events()
        by_seg: dict[str, ScheduledEvent] = {}
        for ev in events:
            sid = _extract_segment_id(getattr(ev, "location", None))
            if sid:
                by_seg[sid] = ev

        created = updated = skipped = 0

        for seg in segments:
            seg_id = seg.get("id")
            if not seg_id:
                continue

            start_dt = _parse_iso_z(seg.get("start_time"))
            end_dt = _parse_iso_z(seg.get("end_time"))
            if not start_dt or not end_dt:
                continue

            name = seg.get("title") or "Untitled Stream"
            desc = f"Playing {seg.get('game') or 'Unknown Game'} on Twitch"
            location = _segment_url(seg_id)

            existing = by_seg.get(seg_id)
            if not existing:
                await guild.create_scheduled_event(
                    name=name,
                    start_time=start_dt,
                    end_time=end_dt,
                    description=desc,
                    privacy_level=discord.PrivacyLevel.guild_only,
                    entity_type=discord.EntityType.external,
                    location=location,
                )
                created += 1
            else:
                changed = (
                    existing.name != name
                    or (existing.start_time and existing.start_time != start_dt)
                    or (existing.end_time and existing.end_time != end_dt)
                    or getattr(existing, "location", None) != location
                )
                if changed:
                    await existing.edit(
                        name=name,
                        start_time=start_dt,
                        end_time=end_dt,
                        description=desc,
                        location=location,
                    )
                    updated += 1
                else:
                    skipped += 1

            # best-effort record
            try:
                await execute(
                    """
                    INSERT INTO synced_events (external_id, origin, guild_id, title, description, start_time, end_time, location, last_sync_source)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (external_id, guild_id)
                    DO UPDATE SET
                      title=EXCLUDED.title,
                      description=EXCLUDED.description,
                      start_time=EXCLUDED.start_time,
                      end_time=EXCLUDED.end_time,
                      location=EXCLUDED.location,
                      last_sync_source=EXCLUDED.last_sync_source,
                      updated_at=NOW()
                    """,
                    (seg_id, "twitch", str(guild.id), name, desc, start_dt, end_dt, location, "twitch_import"),
                )
            except Exception:
                pass

        await ctx.send(f"✅ Twitch import done. Created: {created}, Updated: {updated}, Unchanged: {skipped}.")


async def setup(bot):
    await bot.add_cog(ScheduleSync(bot))