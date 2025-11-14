# bot/commands/schedule_sync.py
import discord, asyncio, datetime as dt
from discord import app_commands
from discord.ext import commands, tasks
import os
from psycopg.rows import dict_row
import psycopg

from bot.workers import messiah_worker as worker

DATABASE_URL = os.getenv("DATABASE_URL")

class ScheduleSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._loop = self.sync_loop
        self._loop.start()

    def cog_unload(self):
        self._loop.cancel()

    @tasks.loop(minutes=5)
    async def sync_loop(self):
        try:
            await worker.run_global_sync(self.bot)
        except Exception as e:
            print(f"[ScheduleSync] loop error: {e}")

    @sync_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    # ------------------- COMMANDS -------------------

    @app_commands.command(name="schedule_sync", description="Manually run Twitch↔Discord schedule sync")
    async def schedule_sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        result = await worker.run_sync_for_guild(self.bot, interaction.guild)
        await interaction.followup.send(f"✅ {result}", ephemeral=True)

    @app_commands.command(name="schedule_sync_enable", description="Enable or disable schedule sync for this server")
    async def schedule_sync_enable(self, interaction: discord.Interaction, enabled: bool):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO schedule_sync_settings (guild_id, enabled) VALUES (%s,%s) "
                "ON CONFLICT (guild_id) DO UPDATE SET enabled=EXCLUDED.enabled",
                (str(interaction.guild.id), enabled),
            )
        await interaction.response.send_message(f"🔁 Sync {'enabled' if enabled else 'disabled'}", ephemeral=True)

    @app_commands.command(name="schedule_sync_status", description="Show current Twitch↔Discord sync stats")
    async def schedule_sync_status(self, interaction: discord.Interaction):
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM schedule_sync_settings WHERE guild_id=%s", (str(interaction.guild.id),))
            row = await cur.fetchone()
        embed = discord.Embed(title="📅 Schedule Sync Status", color=discord.Color.blurple())
        if not row:
            embed.description = "No sync settings found."
        else:
            embed.add_field(name="Enabled", value=str(row.get("enabled", False)))
            embed.add_field(name="Default Channel", value=str(row.get("default_channel_id") or "None"))
        embed.set_footer(text="MessiahBot • Sync Overview")
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(ScheduleSync(bot))
