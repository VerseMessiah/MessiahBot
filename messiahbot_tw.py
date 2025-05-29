# messiah_twitchbot.py
import os
import asyncio
import importlib
from twitchio.ext import commands
from dotenv import load_dotenv

load_dotenv()

BOT_NICK = os.getenv("TWITCH_BOT_USERNAME")
TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
CHANNEL = os.getenv("TWITCH_CHANNEL")

bot = commands.Bot(
    token=TOKEN,
    prefix="!",
    initial_channels=[CHANNEL]
)

@bot.event
async def event_ready():
    print(f"✅ Twitch bot connected as {BOT_NICK}!")

async def load_commands():
    commands_dir = "./commands_tw"
    for filename in os.listdir(commands_dir):
        if filename.endswith(".py") and filename not in loaded:
            try:
                module = importlib.import_module(f"commands_tw.{filename[:-3]}")
                bot.add_cog(module.Pupperz(bot))  # <- no await here
                print(f"✅ Loaded cog: {filename}")
                loaded.add(filename)
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")


async def main():
    await load_commands()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())