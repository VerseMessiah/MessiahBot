import discord
from discord.ext import commands
import random

class Pupperz(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_repo_url = "https://raw.githubusercontent.com/VerseMessiah/pupperz/main/"
        self.image_filenames = [
            "frito1.jpg",
            "meatball2.jpg",
            "chalupa3.jpg",
            "gouda4.jpg"
        ]

    @commands.command(name="pupperz")
    async def pupperz(self, ctx):
        if not self.image_filenames:
            await ctx.send("🐾 No pupperz found.")
            return

        chosen_file = random.choice(self.image_filenames)
        image_url = self.image_repo_url + chosen_file

        await ctx.send(f"🐶 Behold a blessed pupper: {image_url}")
        await ctx.send("/trigger pupperz")

async def setup(bot):
    await bot.add_cog(Pupperz(bot))
