import datetime as dt
import time

import aiohttp
import discord
from discord import Guild, ScheduledEvent
from discord.ext import commands

from bot.integrations.db import fetch_one, execute
from bot.integrations.twitch_api import TwitchAPI


def normalize_twitch_segment(raw: dict) -> dict:
    """Normalize a Twitch schedule segment into a bot-friendly shape."""
    category = raw.get("category") or {}
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Stream",
        "game": category.get("name") or "Unknown Game",
        "start_time": raw.get("start_time"),
        "end_time": raw.get("end_time"),
        "is_recurring": bool(raw.get("is_recurring")),
    }


def _parse_iso_z(s: str | None) -> dt.datetime | None:
    """Parse Twitch ISO timestamps like '2026-03-06T03:00:00Z' to aware datetime."""
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _segment_url(segment_id: str, login: str = "versemessiah") -> str:
    # Lightweight mapping: store the segment id in the Discord event location URL
    return f"https://twitch.tv/{login}?segment_id={segment_id}"


def _extract_segment_id(location: str | None) -> str | None:
    if not location:
        return None
    key = "segment_id="
    if key not in location:
        return None
    try:
        return location.split(key, 1)[1].split("&", 1)[0].strip() or None
    except Exception:
        return None


class ScheduleSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="debug_events")
    @commands.guild_only()
    async def debug_events(self, ctx):
        """Debug: list scheduled events in this Discord guild."""
        guild: Guild = ctx.guild
        events = await guild.fetch_scheduled_events()

        if not events:
            await ctx.send("ℹ️ No scheduled events found in this server.")
            return

        lines = []
        for ev in events:
            start = ev.start_time.isoformat() if ev.start_time else "(no start_time)"
            loc = getattr(ev, "location", None) or "(no location)"
            lines.append(f"• {ev.name} | id={ev.id} | {start} | {loc}")

        await ctx.send("**Discord scheduled events:**\n" + "\n".join(lines))

    @commands.command(name="debug_twitch")
    @commands.guild_only()
    @commands.cooldown(1, 60, commands.BucketType.guild)
    async def debug_twitch(self, ctx):
        """Debug: pull Twitch schedule segments for this guild and print a preview."""
        guild: Guild = ctx.guild

        # Simple in-memory cache to avoid Twitch 429 while testing
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
            await ctx.send("❌ No Twitch connection found for this server. Connect Twitch first.")
            return

        broadcaster_id = row["twitch_user_id"]
        access_token = row["access_token"]
        refresh_token = row["refresh_token"]

        async with aiohttp.ClientSession() as session:
            api = TwitchAPI(session)
            try:
                raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=10)
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "too many requests" in msg:
                    await ctx.send("⏳ Twitch rate limited us (429). Try again in a minute.")
                    return
                if "401" in msg or "unauthorized" in msg or "oauth" in msg:
                    new = await api.refresh_user_token(refresh_token)
                    access_token = new["access_token"]
                    refresh_token = new.get("refresh_token") or refresh_token

                    await execute(
                        """
                        UPDATE twitch_tokens
                        SET access_token = %s, refresh_token = %s
                        WHERE guild_id = %s AND twitch_user_id = %s
                        """,
                        (access_token, refresh_token, str(guild.id), broadcaster_id),
                    )

                    raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=10)
                else:
                    raise

        segments = [normalize_twitch_segment(s) for s in (raw_segments or [])]
        if not segments:
            await ctx.send("ℹ️ Twitch schedule is empty (no segments).")
            return

        lines = []
        for s in segments:
            title = s.get("title") or "(no title)"
            start = s.get("start_time") or ""
            end = s.get("end_time") or ""
            seg_id = (s.get("id") or "")
            seg_short = seg_id[:18] + "…" if len(seg_id) > 18 else seg_id
            lines.append(f"• {start} → {end} | {title} | id: {seg_short}")

        msg_out = "**Twitch schedule (next 10):**\n" + "\n".join(lines)
        cache[str(guild.id)] = {"ts": now, "msg": msg_out}
        self.bot._twitch_schedule_cache = cache
        await ctx.send(msg_out)

    @commands.command(name="twitch_import")
    @commands.guild_only()
    @commands.cooldown(1, 120, commands.BucketType.guild)
    async def twitch_import(self, ctx):
        """Import Twitch schedule segments into Discord scheduled events (external)."""
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
            await ctx.send("❌ No Twitch connection found for this server. Connect Twitch first.")
            return

        broadcaster_id = row["twitch_user_id"]
        access_token = row["access_token"]
        refresh_token = row["refresh_token"]

        async with aiohttp.ClientSession() as session:
            api = TwitchAPI(session)
            try:
                raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=25)
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "too many requests" in msg:
                    await ctx.send("⏳ Twitch rate limited us (429). Try again in a minute.")
                    return
                if "401" in msg or "unauthorized" in msg or "oauth" in msg:
                    new = await api.refresh_user_token(refresh_token)
                    access_token = new["access_token"]
                    refresh_token = new.get("refresh_token") or refresh_token
                    await execute(
                        """
                        UPDATE twitch_tokens
                        SET access_token = %s, refresh_token = %s
                        WHERE guild_id = %s AND twitch_user_id = %s
                        """,
                        (access_token, refresh_token, str(guild.id), broadcaster_id),
                    )
                    raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=25)
                else:
                    raise

        segments = [normalize_twitch_segment(s) for s in (raw_segments or [])]
        if not segments:
            await ctx.send("ℹ️ Twitch schedule is empty (no segments).")
            return

        events = await guild.fetch_scheduled_events()
        by_segment_id: dict[str, ScheduledEvent] = {}
        for ev in events:
            seg_id = _extract_segment_id(getattr(ev, "location", None))
            if seg_id:
                by_segment_id[seg_id] = ev

        created = 0
        updated = 0
        skipped = 0

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

            existing = by_segment_id.get(seg_id)
            if existing is None:
                try:
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
                except Exception as e:
                    await ctx.send(f"⚠️ Failed to create event for segment {seg_id[:12]}…: {type(e).__name__}: {e}")
                    continue
            else:
                try:
                    changed = False
                    if existing.name != name:
                        changed = True
                    if existing.start_time and existing.start_time != start_dt:
                        changed = True
                    if existing.end_time and existing.end_time != end_dt:
                        changed = True
                    if getattr(existing, "location", None) != location:
                        changed = True

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
                except Exception as e:
                    await ctx.send(f"⚠️ Failed to update event for segment {seg_id[:12]}…: {type(e).__name__}: {e}")
                    continue

            # Best-effort tracking in synced_events
            try:
                await execute(
                    """
                    INSERT INTO synced_events (external_id, origin, guild_id, title, description, start_time, end_time, location, last_sync_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (external_id, guild_id)
                    DO UPDATE SET
                      title = EXCLUDED.title,
                      description = EXCLUDED.description,
                      start_time = EXCLUDED.start_time,
                      end_time = EXCLUDED.end_time,
                      location = EXCLUDED.location,
                      last_sync_source = EXCLUDED.last_sync_source,
                      updated_at = NOW()
                    """,
                    (
                        seg_id,
                        "twitch",
                        str(guild.id),
                        name,
                        desc,
                        start_dt,
                        end_dt,
                        location,
                        "twitch_import",
                    ),
                )
            except Exception:
                pass

        await ctx.send(f"✅ Twitch import done. Created: {created}, Updated: {updated}, Unchanged: {skipped}.")


async def setup(bot):
    await bot.add_cog(ScheduleSync(bot))