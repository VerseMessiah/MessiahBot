# bot/messiahbot_dc.py
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Intents
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True                   # some ops need this
INTENTS.guild_scheduled_events = True    # needed for Discord Events sync
INTENTS.message_content = True          # needed for legacy commands (not slash commands)

class MessiahBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",  # only used for legacy cmds; slash commands are primary
            intents=INTENTS,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        # Load cogs (server builder + schedule sync)
        extensions = [
            "bot.commands_messiah_dc.server_builder",
            "bot.commands_messiah_dc.schedule_sync",   # <-- make sure this file exists
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {type(e).__name__}: {e}")

        # Sync slash commands globally
        try:
            await self.tree.sync()
            print("✅ Slash commands synced")
        except Exception as e:
            print("❌ Slash sync error:", e)

bot = MessiahBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print(f"[Messiah] app command error: {type(error).__name__}: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Something went wrong running that command.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Something went wrong running that command.", ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_ready():
    print(f"✨ MessiahBot is online as {bot.user} (ID: {bot.user.id})")

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("❌ Missing DISCORD_BOT_TOKEN")
    bot.run(DISCORD_BOT_TOKEN)
