import os
import asyncio
import datetime
import aiohttp
import psycopg
from discord import Client, Intents, Guild, ScheduledEvent, ScheduledEventPrivacyLevel, ScheduledEventEntityType
from twitchAPI.twitch import Twitch
from twitchAPI.helper import first

DATABASE_URL = os.getenv("DATABASE_URL")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

SYNC_INTERVAL = 60 * 30  # every 30 minutes


class SyncWorker(Client):
    def __init__(self):
        intents = Intents.default()
        intents.guilds = True
        intents.guild_scheduled_events = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"✅ SyncWorker logged in as {self.user}")
        await self.run_sync_cycle()
        await self.close()

    async def run_sync_cycle(self):
        """Main loop to pull Twitch schedules and mirror them as Discord events."""
        try:
            async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        SELECT guild_id, twitch_id, discord_channel_id, id
                        FROM schedule_sync_settings
                        WHERE sync_enabled = TRUE
                    """)
                    rows = await cur.fetchall()
        except Exception as e:
            print(f"❌ Database error: {e}")
            return

        if not rows:
            print("⚠️ No guilds found with sync enabled.")
            return

        twitch = await Twitch(TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET)
        print(f"🔄 Found {len(rows)} guilds to sync.")

        for guild_id, twitch_id, channel_id, setting_id in rows:
            try:
                await self.sync_guild_schedule(twitch, guild_id, twitch_id, channel_id, setting_id)
            except Exception as e:
                print(f"⚠️ Sync error for guild {guild_id}: {e}")
                await self.log_error(setting_id, str(e))

        await twitch.close()
        print("✅ Sync complete for all guilds.")

    async def sync_guild_schedule(self, twitch: Twitch, guild_id: str, twitch_id: str, channel_id: str, setting_id: int):
        """Fetch Twitch schedule and create/update Discord events."""
        guild: Guild = self.get_guild(int(guild_id))
        if not guild:
            print(f"⚠️ Bot not in guild {guild_id}, skipping.")
            return

        async for event in self.get_guild_scheduled_events(guild):
            pass  # ensure we have perms

        schedule = await first(twitch.get_channel_schedule(broadcaster_id=twitch_id))
        if not schedule or not schedule.segments:
            print(f"ℹ️ No schedule found for Twitch ID {twitch_id}")
            return

        async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
            async with conn.cursor() as cur:
                for seg in schedule.segments:
                    title = seg.title or "Twitch Stream"
                    start = datetime.datetime.fromisoformat(seg.start_time.replace("Z", "+00:00"))
                    end = datetime.datetime.fromisoformat(seg.end_time.replace("Z", "+00:00"))
                    external_id = seg.id

                    await cur.execute(
                        "SELECT id FROM synced_events WHERE external_id = %s AND guild_id = %s",
                        (external_id, guild_id),
                    )
                    existing = await cur.fetchone()

                    if not existing:
                        await guild.create_scheduled_event(
                            name=title,
                            start_time=start,
                            end_time=end,
                            entity_type=ScheduledEventEntityType.external,
                            privacy_level=ScheduledEventPrivacyLevel.guild_only,
                            location=f"https://twitch.tv/{twitch_id}"
                        )
                        print(f"🆕 Created Discord event for {title}")
                    else:
                        print(f"🔁 Event already exists: {title}")

                await cur.execute("""
                    UPDATE schedule_sync_settings
                    SET last_sync = NOW(), last_error = NULL
                    WHERE id = %s
                """, (setting_id,))
                await conn.commit()

    async def log_error(self, setting_id: int, message: str):
        """Log any sync errors in the DB."""
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE schedule_sync_settings
                    SET last_error = %s
                    WHERE id = %s
                """, (message[:250], setting_id))
                await conn.commit()


async def main():
    client = SyncWorker()
    await client.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
