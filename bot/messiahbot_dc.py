import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

print("🧠 MessiahBot module loaded")

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

INTENTS = discord.Intents.all()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.guild_scheduled_events = True
INTENTS.message_content = True

class MessiahBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=INTENTS,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        print("🚀 setup_hook triggered (from inside MessiahBot)")
        extensions = [
            "bot.commands.server_builder",
            "bot.commands.schedule_sync_cog",
            "bot.commands.plex_commands",
        ]
        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {type(e).__name__}: {e}")

        try:
            await self.tree.sync()
            print("✅ Slash commands synced")
        except Exception as e:
            print(f"❌ Slash sync error: {e}")

bot = MessiahBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print(f"[Messiah] app command error: {type(error).__name__}: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Something went wrong.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Something went wrong.", ephemeral=True)
    except Exception:
        pass

@bot.event
async def on_ready():
    print(f"✨ MessiahBot is online as {bot.user} (ID: {bot.user.id})")

if __name__ == "__main__":
    print("🔑 Starting MessiahBot worker...")
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("❌ Missing DISCORD_BOT_TOKEN")
    bot.run(DISCORD_BOT_TOKEN)
