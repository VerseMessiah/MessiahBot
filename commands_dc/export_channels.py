import json
from discord.ext import commands

CHANNEL_CONFIG_FILE = "channel_config.json"

class ExportChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="exportchannels")
    @commands.has_permissions(manage_channels=True)
    async def export_channels(self, ctx):
        config = {}
        for channel in ctx.guild.text_channels:
            config[channel.name] = channel.topic if channel.topic else ""

        with open(CHANNEL_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        await ctx.send(f"📤 Exported {len(config)} channels to `{CHANNEL_CONFIG_FILE}`.")

async def setup(bot):
    await bot.add_cog(ExportChannels(bot))
