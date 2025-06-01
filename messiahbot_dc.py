import discord
from discord.ext import commands
import os
import threading
from dashboard_dc.app import app as flask_app
import dotenv

# Load environment variables from .env
dotenv.load_dotenv()
print("DEBUG: After load_dotenv, DISCORD_BOT_TOKEN =", os.getenv("DISCORD_BOT_TOKEN"))

# Set up Discord bot
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🕊️ {bot.user} is live.")

# Load cogs from /commands_dc
@bot.event
async def setup_hook():
    print("[setup_hook] Starting to load cogs…")
    commands_dir = "commands_dc"
    loaded = set()

    for filename in os.listdir(commands_dir):
        if filename.endswith(".py") and filename not in loaded:
            try:
                module_path = f"{commands_dir}.{filename[:-3]}"
                await bot.load_extension(module_path)
                print(f"✅ Loaded cog: {filename}")
                loaded.add(filename)
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")

    print("[setup_hook] Finished loading cogs.")

# Start Flask dashboard in a separate thread
def run_flask():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))

if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN is not set. Exiting.")
        exit(1)

    # Start the Flask server
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Run the bot
    bot.run(token)
