# bot/messiahbot_dc.py
import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv

print("🧠 MessiahBot module loaded")

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")  # make sure this is set in env group

# ---- Intents ----
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
        print("🚀 setup_hook triggered (inside MessiahBot)")
        extensions = [
            "bot.commands_messiah_dc.server_builder",
            "bot.commands_messiah_dc.schedule_sync",
            "bot.commands_messiah_dc.plex_commands",
        ]

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {type(e).__name__}: {e}")

        # Slash command sync — global + guild fallback
        try:
            if DISCORD_GUILD_ID:
                guild_obj = discord.Object(id=int(DISCORD_GUILD_ID))
                synced = await self.tree.sync(guild=guild_obj)
                print(f"✅ Synced {len(synced)} guild slash command(s) to GUILD {DISCORD_GUILD_ID}")
            else:
                synced = await self.tree.sync()
                print(f"✅ Synced {len(synced)} global slash command(s)")
        except Exception as e:
            print(f"❌ Slash sync error: {type(e).__name__}: {e}")


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
    print("🔑 Starting MessiahBot worker...")
    if not DISCORD_BOT_TOKEN:
        raise SystemExit("❌ Missing DISCORD_BOT_TOKEN")
    bot.run(DISCORD_BOT_TOKEN)
