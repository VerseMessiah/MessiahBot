import discord
from discord.ext import commands, tasks
import os
import threading
from dashboard_dc.app import app as flask_app
import dotenv
import datetime
import asyncio
from twitch_api import TwitchAPI

# ===== DEBUG LINES =====
print("DEBUG: Current working directory:", os.getcwd())
print("DEBUG: Files in cwd →", os.listdir(os.getcwd()))
print("DEBUG: .env exists?", os.path.exists(".env"))
# ===== END DEBUG =====

# Load environment variables from .env
dotenv.load_dotenv()
print("DEBUG: After load_dotenv, DISCORD_BOT_TOKEN =", os.getenv("DISCORD_BOT_TOKEN"))

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

twitch = TwitchAPI()
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))  # Add this to your .env

# Sync Twitch schedule on a daily timer (9am server time)
@tasks.loop(time=datetime.time(hour=9, minute=0))
async def sync_schedule_daily():
    await sync_schedule()
    print("✅ Daily Twitch schedule sync complete.")

# Manual command to trigger sync
@bot.command()
async def syncschedule(ctx):
    await sync_schedule()
    await ctx.send("✅ Twitch schedule synced to Discord!")

async def sync_schedule():
    try:
        segments = await twitch.get_schedule()
        guild = discord.utils.get(bot.guilds, id=DISCORD_GUILD_ID)

        for seg in segments:
            start_time = datetime.datetime.fromisoformat(seg["start_time"].replace("Z", "+00:00"))
            title = seg["title"]
            location = f"https://twitch.tv/{os.getenv('TWITCH_USERNAME')}"
            description = seg.get("canceled_until", None)
            if description:
                continue  # Skip cancelled segments

            # Optional: Check for duplicates before creating new event

            await guild.create_scheduled_event(
                name=title,
                start_time=start_time,
                end_time=None,
                description="Stream synced from Twitch schedule",
                location=location,
                privacy_level=discord.PrivacyLevel.guild_only,
                entity_type=discord.EntityType.external
            )

        print("✅ Finished syncing Twitch segments to Discord.")
    except Exception as e:
        print(f"❌ Error syncing schedule: {e}")

@bot.event
async def on_ready():
    print(f"🕊️ {bot.user} is live.")
    sync_schedule_daily.start()

# Load cogs from /commands_dc
@bot.event
async def setup_hook():
    print("[setup_hook] Starting to load cogs…")
    commands_dir = "commands_dc"
    loaded = set()

    for filename in os.listdir(commands_dir):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue

        module_path = f"{commands_dir}.{filename[:-3]}"
        try:
            await bot.load_extension(module_path)
            print(f"✅ Loaded cog: {filename}")
            loaded.add(filename)
        except Exception as e:
            print(f"⚠️ Skipped loading {filename}: {e}")

    print("[setup_hook] Finished loading cogs.")

# Start Flask dashboard in a separate thread
def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN is not set. Exiting.")
        exit(1)

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    bot.run(token)
