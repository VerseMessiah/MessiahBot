# commands_dc/export_channels.py

import json
import os
from discord.ext import commands

CHANNEL_CONFIG_FILE = "channel_config.json"

class ExportChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="exportchannels")
    @commands.has_permissions(manage_channels=True)
    async def export_channels(self, ctx):
        # Gather all text channels and their topics
        config = {ch.name: (ch.topic or "") for ch in ctx.guild.text_channels}

        # Debug: show where the file is being written
        abs_path = os.path.abspath(CHANNEL_CONFIG_FILE)
        print(f"[exportchannels] Writing to → {abs_path}. Entries: {len(config)}")

        # Write to channel_config.json
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        await ctx.send(f"📤 Exported {len(config)} channels to `{CHANNEL_CONFIG_FILE}`.")

async def setup(bot):
    await bot.add_cog(ExportChannels(bot))


