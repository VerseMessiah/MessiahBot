import discord
from discord.ext import commands
import os
import asyncio

bot = commands.Bot(command_prefix='!', intents=discord.Intents.all())

@bot.event
async def on_ready():
    print(f"🕊️ MessiahBot is live as {bot.user}")

async def load_cogs():
    for filename in os.listdir('./commands'):
        if filename.endswith('.py') and not filename.startswith('_'):
            try:
                await bot.load_extension(f'commands.{filename[:-3]}')
                print(f"✅ Loaded cog: {filename}")
            except Exception as e:
                print(f"❌ Failed to load cog {filename}: {e}")

async def main():
    async with bot:
        await load_cogs()
        await bot.start('MTM3NDgyMDEzNTk5MDQ2MDQ0Ng.GRnGrv.1p5Toswm3dtbt5DQZdQN4Hn4Uwp6NV-gGyTPN8')  # Replace this with your real token

asyncio.run(main())

