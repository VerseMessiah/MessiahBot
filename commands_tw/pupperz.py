# pupperz.py
import random
import aiohttp
from twitchio.ext import commands

GITHUB_API = "https://api.github.com/repos/VerseMessiah/pupperz/contents/"
REPO_URL = "https://raw.githubusercontent.com/VerseMessiah/pupperz/main/"

class Pupperz(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    async def get_images(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(GITHUB_API) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return [
                        f"{REPO_URL}{file['name']}"
                        for file in data
                        if file["name"].lower().endswith((".png", ".jpg", ".jpeg"))
                    ]
                return []

    @commands.command(name="pupperz")
    async def pupperz(self, ctx):
        images = await self.get_images()
        if images:
            chosen = random.choice(images)
            await ctx.send(f"{ctx.author.name} summoned a divine pupper 🐶: {chosen}")
            # optionally trigger Triggerfyre here
        else:
            await ctx.send("Couldn't fetch pupper pics right now 🥺")
