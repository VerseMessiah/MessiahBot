import discord
from discord.ext import commands

class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="createroles")
    @commands.has_permissions(administrator=True)
    async def createroles(self, ctx):
        guild = ctx.guild  # Get the server where the command was used

        roles = [
            "Aries", "Taurus", "Gemini", "Cancer", "Leo",
            "Virgo", "Libra", "Scorpio", "Sagittarius", "Capricorn",
            "Aquarius", "Pisces", "PC", "Mac", "Playstation", "Xbox", "Switch",
            "iOS", "Android", "ey/em/eir", "he/him/his", "hey/hem/heir", "she/her/hers",
            "they/them/their", "xe/xem/xyr", "ze/zir/zir", "North America", "South America",
            "Europe", "Asia", "Africa", "Australia", "Straight", "Gay", "Lesbian", "Bisexual",
            "Asexual", "Pansexual", "Cisgender", "Non-binary", "Transgender"
        ]

        created = []
        skipped = []

        for role_name in roles:
            existing = discord.utils.get(guild.roles, name=role_name)
            if existing:
                skipped.append(role_name)
            else:
                await guild.create_role(name=role_name)
                created.append(role_name)

        await ctx.send(f"✅ Created roles: {', '.join(created)}")
        if skipped:
            await ctx.send(f"⚠️ Already existed: {', '.join(skipped)}")

# Register this cog
async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
