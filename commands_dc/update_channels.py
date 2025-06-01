import json
import os
import discord
from discord.ext import commands

CHANNEL_CONFIG_FILE = "channel_config.json"
MOD_LOG_CHANNEL = "papal-planning"  # Change this if needed

class UpdateChannels(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="updatechannels")
    @commands.has_permissions(manage_channels=True)
    async def update_channels(self, ctx):
        if not os.path.exists(CHANNEL_CONFIG_FILE):
            await ctx.send(f"⚠️ `{CHANNEL_CONFIG_FILE}` not found. Use `!exportchannels` first.")
            return

        with open(CHANNEL_CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)

        log_messages = []
        updated_count = 0

        for name, new_topic in config.items():
            channel = discord.utils.get(ctx.guild.text_channels, name=name)
            if channel:
                current_topic = channel.topic or ""
                if current_topic.strip() != new_topic.strip():
                    await channel.edit(topic=new_topic)
                    log_messages.append(f"✅ Updated: #{name}")
                    updated_count += 1
                else:
                    log_messages.append(f"➖ Skipped (no change): #{name}")
            else:
                log_messages.append(f"⚠️ Not found: #{name}")

        await ctx.send(f"📖 Channel topics update complete. `{updated_count}` updated.")

        modlog = discord.utils.get(ctx.guild.text_channels, name=MOD_LOG_CHANNEL)
        if modlog:
            await modlog.send("📝 **Channel topic update log:**\n" + "\n".join(log_messages))

async def setup(bot):
    await bot.add_cog(UpdateChannels(bot))

