# bot/messiahbot_dc.py
import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.members = True  # needed for some operations (nicknames, etc.)

class MessiahBot(commands.Bot):
    async def setup_hook(self):
        # Load cogs
        await self.load_extension("bot.commands_messiah_dc.server_builder")
        # Sync slash commands
        try:
            await self.tree.sync()
            print("✅ Slash commands synced")
        except Exception as e:
            print("❌ Slash sync error:", e)

bot = MessiahBot(command_prefix="!", intents=INTENTS)

# somewhere central (e.g., in messiahbot_dc.py after creating bot)
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

