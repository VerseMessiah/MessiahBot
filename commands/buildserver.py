import discord
from discord.ext import commands
import asyncio

class BuildServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    @commands.has_permissions(administrator=True)
    async def buildserver(self, ctx):
        guild = ctx.guild
        await ctx.send("🛠️ Constructing The Vatican’t...")

        role_colors = {
            "Anointed": discord.Color.from_rgb(173, 216, 230),
            "Disciple": discord.Color.from_rgb(146, 132, 246),
            "Holy Jester": discord.Color.from_rgb(255, 87, 187),
            "Priest": discord.Color.from_rgb(249, 200, 14),
            "Messiah": discord.Color.from_rgb(0, 96, 199),
            "Pilgrims": discord.Color.from_rgb(58, 58, 58)
        }

        for role_name, color in role_colors.items():
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                try:
                    await role.edit(colour=color)
                except:
                    pass
            else:
                await guild.create_role(name=role_name, colour=color)

        structure = {
            "📜 Sanctified Entry": ["📖・messiahs-commandments", "🕯️・baptismal-font", "🚪・pilgrim's-gate", "🌈・pick-your-aura"],
            "⚖️ The Ministry of Mayhem": ["🗂️・papal-planning", "📢・divine-decrees", "⛔・banishment-records"]
        }

        for category_name, channels in structure.items():
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                cat = await guild.create_category(category_name)

            for ch_name in channels:
                existing_ch = discord.utils.get(guild.channels, name=ch_name)
                if existing_ch:
                    if existing_ch.category != cat:
                        await existing_ch.edit(category=cat)
                        print(f"🔁 Moved {ch_name} to {category_name}")
                    continue

                try:
                    if ch_name == "📖・messiahs-commandments":
                        await guild.create_text_channel(ch_name, category=cat)
                    elif "decrees" in ch_name or "planning" in ch_name:
                        await guild.create_text_channel(ch_name, category=cat)
                    elif "banishment" in ch_name:
                        await guild.create_text_channel(ch_name, category=cat)
                    await asyncio.sleep(0.2)
                except Exception as e:
                    print(f"❌ Could not create or move channel {ch_name}: {e}")

        await ctx.send("✅ Messiah’s infrastructure adjusted. Essentials are in place.")

async def setup(bot):
    await bot.add_cog(BuildServer(bot))
