import datetime as dt
import time

import aiohttp
import discord
from discord import Guild, ScheduledEvent
from discord.ext import commands

from bot.integrations.db import fetch_one, execute
from bot.integrations.twitch_api import TwitchAPI


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

        row = await fetch_one(
            """
            SELECT twitch_user_id, access_token, refresh_token
            FROM twitch_tokens
            WHERE guild_id = %s
            """,
            (str(guild.id),),
        )
        if not row:
            await ctx.send("❌ No Twitch connection found for this server.")
            return

        broadcaster_id = row["twitch_user_id"]
        access_token = row["access_token"]

        async with aiohttp.ClientSession() as session:
            api = TwitchAPI(session)
            raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=10)

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

        row = await fetch_one(
            """
            SELECT twitch_user_id, access_token, refresh_token
            FROM twitch_tokens
            WHERE guild_id = %s
            """,
            (str(guild.id),),
        )
        if not row:
            await ctx.send("❌ No Twitch connection found for this server.")
            return

        broadcaster_id = row["twitch_user_id"]
        access_token = row["access_token"]

        async with aiohttp.ClientSession() as session:
            api = TwitchAPI(session)
            raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=25)

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