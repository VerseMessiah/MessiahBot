import aiohttp

from discord import Guild, ScheduledEvent
from discord.ext import commands

from bot.integrations.db import fetch_one, execute
from bot.integrations.twitch_api import TwitchAPI



async def sync_events(guild: Guild):
    events: list[ScheduledEvent] = await guild.fetch_scheduled_events()
    
    for ev in events:
        print("EVENT:", ev.name)
        print(" entity_type:", ev.entity_type)
        print(" has location attr:", hasattr(ev, "location"))
        print(" location value:", ev.location)

#twitch_event = {
    #"id": str,
    #"title": str,
    #"game": str,
    #"starts_at": str,
    #"recurring": bool
#}

#discord_event = {
    #"name": str,
    #"description": str,
    #"scheduled_start_time": str,
    #"entity_type": "external",
    #"location": {
        #"location": str
    #}
#}

def normalize_twitch(raw):
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled Stream",
        "game": (raw.get("category") or {}).get("name", "Unknown Game"),
        "starts_at": raw.get("start_time"),
        "recurring": raw.get("is_recurring")
    }

def normalize_discord(ev: ScheduledEvent) -> dict:
    
    return {
        "id": str(ev.id),
        "name": ev.name,
        "description": ev.description or "",
        "starts_at": ev.start_time.isoformat() if ev.start_time else None,
        "location": ev.location if ev.location else None,
    }


def twitch_to_discord(evt: dict) -> dict:
    return {
        "name": evt["title"],
        "description": f"Playing {evt['game']} on Twitch",
        "scheduled_start_time": evt["starts_at"],
        "entity_type": "external",
        "location": {
            "location": f"https://twitch.tv/versemessiah?event_id={evt['id']}"
        }
    } 

#raw_twitch = [
    #{
        #"id": "abc123",
        #"title": None,
        #"category": None, 
        #"start_time": "2026-02-01T02:00:00Z",
        #"is_recurring": False
    #},
    #{
        #"id": "def456",
        #"title": "Late Night Stream",
        #"category": {"name": "Fortnite"},
        #"start_time": "2026-02-02T03:00:00Z",
        #"is_recurring": True
    #}
#]

def get_event_id(location: str) -> str | None:
    if not location:
        return None
    if "event_id=" not in location:
        return None
    else:
        discord_event_id = location.split("event_id=")[1]
        return discord_event_id

def needs_update(twitch_evt, discord_evt) -> bool:
    if twitch_evt["title"] != discord_evt["name"]:
        return True
    if twitch_evt["starts_at"] != discord_evt["scheduled_start_time"]:
        return True
    expected_description = f"Playing {twitch_evt['game']} on Twitch"
    if expected_description != discord_evt["description"]:
        return True
    
    return False


class ScheduleSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="debug_events")
    async def debug_events(self, ctx):
        guild = ctx.guild
        if not guild:
            await ctx.send("❌ No guild context.")
            return

        events = await guild.fetch_scheduled_events()

        if not events:
            await ctx.send("ℹ️ No scheduled events found.")
            return

        for ev in events:
            await ctx.send(
                f"**{ev.name}**\n"
                f"type: {ev.entity_type}\n"
                f"location: {ev.location}"
            )
    
    @commands.command(name="debug_twitch")
    @commands.guild_only()
    async def debug_twitch(self, ctx):
        row = await fetch_one(
            """
            SELECT twitch_user_id, access_token, refresh_token
            FROM twitch_tokens
            WHERE guild_id = %s
            """,
            (str(ctx.guild.id),),
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
                        (access_token, refresh_token, str(ctx.guild.id), broadcaster_id),
                    )

                    raw_segments = await api.get_schedule_segments(broadcaster_id, access_token, first=10)
                else:
                    raise
            
            if not raw_segments:
                await ctx.send("ℹ️ Twitch schedule is empty (no segments found)")
                return
            
            lines = []
            for s in raw_segments:
                seg_id = (s.get("id") or "")
                seg_short = seg_id[:18] + "…" if len(seg_id) > 18 else seg_id
                title = s.get("title") or "(no title)"
                start = s.get("start_time") or ""
                end = s.get("end_time") or ""
                lines.append(f"• {start} -> {end} | {title} | id: {seg_short}")

            await ctx.send("**Twitch schedule (next 10):**\n" + "\n".join(lines))

async def setup(bot):
    await bot.add_cog(ScheduleSync(bot))
