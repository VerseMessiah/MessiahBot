# messiah_twitchbot.py
import os
import asyncio
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
        if filename.endswith(".py"):
            module = importlib.import_module(f"commands_tw.{filename[:-3]}")
            if hasattr(module, "Pupperz"):
                await bot.add_cog(module.Pupperz(bot))

async def main():
    await load_extensions()
    await bot.run()

asyncio.run(main())

