# bot/messiahbot_dc.py
"""
MessiahBot main Discord service
--------------------------------
Responsible for:
 - Connecting to Discord
 - Loading all command cogs (server builder, Plex, schedule sync, etc.)
 - Syncing slash commands
"""

import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

print("🧠 MessiahBot module loaded")

# Load environment
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Intents setup
INTENTS = discord.Intents.all()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.guild_scheduled_events = True
INTENTS.message_content = True

class MessiahBot(commands.Bot):
    """Primary bot instance for Discord service"""

    def __init__(self):
        super().__init__(
            command_prefix="!",  # for legacy commands
            intents=INTENTS,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        """Load all Cogs and sync slash commands."""
        print("🚀 setup_hook triggered (loading extensions)")

        # All Discord-side Cogs go here
        extensions = [
            "bot.commands.server_builder",
            "bot.commands.plex_commands",
            "bot.commands.schedule_sync",        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {type(e).__name__}: {e}")

        # Sync slash commands (global)
        try:
            await self.tree.sync()
            print("✅ Slash commands synced globally")
        except Exception as e:
            print(f"❌ Slash sync error: {e}")

# Instantiate bot
bot = MessiahBot()

# Global error handler for slash/app commands
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print(f"[MessiahBot] app command error: {type(error).__name__}: {error}")
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

# Entrypoint
if __name__ == "__main__":
    print("🔑 Starting MessiahBot worker...")
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("❌ Missing DISCORD_BOT_TOKEN")
    bot.run(DISCORD_BOT_TOKEN)
