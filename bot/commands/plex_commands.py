import os
import discord
from discord import app_commands
from discord.ext import commands
from bot.utils.plex_utils import get_plex_client

class PlexCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="plex_libraries", description="List Plex libraries and item counts")
    async def plex_libraries(self, interaction: discord.Interaction):
        try:
            plex = get_plex_client()
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Plex not configured: {e}", ephemeral=True)
            return

        try:
            sections = plex.library.sections()
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to reach Plex: {e}", ephemeral=True)
            return

        parts = []
        for sec in sections:
            try:
                count = sec.totalSize  # may trigger a query
            except Exception:
                count = "?"

            parts.append(f"• **{sec.title}** — {count} items")

        if not parts:
            msg = "No libraries found."
        else:
            msg = "\n".join(parts)

        await interaction.response.send_message(f"🎬 Plex Libraries:\n{msg}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(PlexCommands(bot))
