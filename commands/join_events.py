import discord
from discord.ext import commands

class JoinEvents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.role_message_id = None

        self.reaction_roles = {
            "🌕": "He/Him",
            "🌑": "She/Her",
            "🌓": "They/Them",
            "🌟": "Xe/Xem",
            "♈": "Aries",
            "♉": "Taurus",
            "♊": "Gemini",
            "♋": "Cancer",
            "♌": "Leo",
            "♍": "Virgo",
            "♎": "Libra",
            "♏": "Scorpio",
            "♐": "Sagittarius",
            "♑": "Capricorn",
            "♒": "Aquarius",
            "♓": "Pisces",
            "🖥️": "PC",
            "🍎": "Mac",
            "📱": "iOS",
            "🤖": "Android",
            "🎮": "Playstation",
            "🕹️": "Xbox",
            "🎲": "Switch"
        }

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            welcome_msg = (
                f"👋 Welcome to **The Vatican’t**, {member.mention}!"
                "Please start by reading <#📖・messiahs-commandments>,then verify yourself by typing `!verify` in a bot channel."
                "After that, visit <#pick-your-aura> to select your pronouns, platform, and vibe. 🪩"
            )
            await member.send(welcome_msg)
        except:
            print(f"❌ Could not DM {member.name}")

        welcome_channel = discord.utils.get(member.guild.text_channels, name="arrival-gate")
        if welcome_channel:
            await welcome_channel.send(f"🕊️ Welcome {member.mention} to The Vatican’t! Bless yourself at the font. 🕯️")

    @commands.command(name="setupaura")
    @commands.has_permissions(administrator=True)
    async def setup_role_picker(self, ctx):
        guild = ctx.guild

        # Create missing roles
        for role_name in self.reaction_roles.values():
            existing = discord.utils.get(guild.roles, name=role_name)
            if not existing:
                await guild.create_role(name=role_name)
                print(f"✅ Created missing role: {role_name}")

        embed = discord.Embed(
            title="✨ Pick Your Aura",
            description=(
                "**React below to choose your roles!**"
                "__Pronouns:__"
                "🌕 He/Him
🌑 She/Her
🌓 They/Them
🌟 Xe/Xem

"
                "__Zodiac:__
"
                "♈ ♉ ♊ ♋ ♌ ♍ ♎ ♏ ♐ ♑ ♒ ♓

"
                "__Platform:__
"
                "🖥️ PC  🍎 Mac  📱 iOS  🤖 Android  🎮 Playstation  🕹️ Xbox  🎲 Switch"
            ),
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url="https://cdn.discordapp.com/attachments/1376152605889396837/1376157669060120626/messiahthumbnail.png?ex=	68344e34&is=6832fcb4&hm=b31e468004c2ace7ab6491bfe9076e1788d6f1d8a797667cba5ff7fdae9dea4f&") # Fun icon - can be changed later

        message = await ctx.send(embed=embed)
        self.role_message_id = message.id

        for emoji in self.reaction_roles.keys():
            await message.add_reaction(emoji)

        await message.pin()
        await ctx.send("✅ Aura picker set up and pinned.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member is None or payload.member.bot:
            return
        if payload.message_id != self.role_message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        role_name = self.reaction_roles.get(str(payload.emoji))
        if role_name:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await payload.member.add_roles(role)
                print(f"✅ Gave {payload.member} the role {role.name}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        guild = self.bot.get_guild(payload.guild_id)
        member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        if payload.message_id != self.role_message_id:
            return

        role_name = self.reaction_roles.get(str(payload.emoji))
        if role_name:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await member.remove_roles(role)
                print(f"🗑️ Removed {role.name} from {member.name}")

async def setup(bot):
    await bot.add_cog(JoinEvents(bot))
