# messiah_twitchbot.py
import os
import random
from twitchio.ext import commands
from dotenv import load_dotenv

load_dotenv()

BOT_NICK = os.getenv("TWITCH_BOT_USERNAME")
TOKEN = os.getenv("TWITCH_OAUTH_TOKEN")
CHANNEL = os.getenv("TWITCH_CHANNEL")
IMAGE_REPO = os.getenv("IMAGE_REPO_URL")

bot = commands.Bot(
    token=TOKEN,
    prefix="!",
    initial_channels=[CHANNEL]
)

@bot.event
async def event_ready():
    print(f"✅ Twitch bot connected as {BOT_NICK}")

@bot.command(name="pupperz")
async def pupperz(ctx):
    # list of known image filenames in your GitHub repo (adjust if needed)
    images = [
        "pupperz1.png", "pupperz2.png", 
        "pupperz3.png", "pupperz4.png", 
        "pupperz5.png", "pupperz6.png", 
        "pupperz7.png", "pupperz8.png", 
        "pupperz9.png", "pupperz10.png", 
    ]
    selected = random.choice(images)
    image_url = f"{IMAGE_REPO}/{selected}"
    await ctx.send(f"{ctx.author.name} has summoned a divine pupper pic 🐶: {image_url}")

bot.run()

