import discord
from discord.ext import commands
import os
import importlib.util

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"🕊️ {bot.user} is live.")

# Load each cog once from /commands
@bot.event
async def setup_hook():
    commands_dir = "commands"
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

# Run the bot with token from .env or Render env
if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv()
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN is not set.")
    else:
        bot.run(token)

with open(filename, "r", encoding="utf-8") as f:
    f.write(messiahbot_code)