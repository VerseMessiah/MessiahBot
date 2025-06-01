import json, os
from discord.ext import commands

CHANNEL_CONFIG_FILE = "channel_config.json"

class ExportChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="exportchannels")
    @commands.has_permissions(manage_channels=True)
    async def export_channels(self, ctx):
        config = {ch.name: (ch.topic or "") for ch in ctx.guild.text_channels}

        # 1) Print where we’re writing
        abs_path = os.path.abspath(CHANNEL_CONFIG_FILE)
        print(f"[exportchannels] Writing to → {abs_path}. Number of entries: {len(config)}")

        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        await ctx.send(f"📤 Exported {len(config)} channels to `{CHANNEL_CONFIG_FILE}` (at `{abs_path}`).")

async def setup(bot):
    await bot.add_cog(ExportChannels(bot))
