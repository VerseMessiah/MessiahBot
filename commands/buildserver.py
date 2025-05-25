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
        await ctx.send("🛠️ Constructing The Vatican’t... (only missing items will be added)")

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
                    print(f"🎨 Updated role: {role_name}")
                except Exception as e:
                    print(f"⚠️ Could not update {role_name}: {e}")
            else:
                await guild.create_role(name=role_name, colour=color)
                print(f"✅ Created role: {role_name}")

        await ctx.send("📁 Roles created/updated. Now checking channels...")

        structure = {
            "📜 Sanctified Entry": ["📖・messiahs-commandments", "🕯️・baptismal-font", "🚪・pilgrim's-gate", "🌈・pick-your-aura"],
            "🏛️ Messiah's Temple": ["💬・verse-chat", "🖼️・meme-scripture", "📸・altar-selfies", "🎞️・divine-clips", "🎙️ prayer-circle"],
            "💔 Blessed & Distressed": ["📢・vent-confessional", "🧠・mental-wellness", "🌌・spiritual-gremlin-hours"],
            "🎮 The Divine Queue": ["🗡️・fortnite-sacrifices", "📜・matchmaking-scrolls", "🕹️・gaymer-grail", "🎮 squad-up"],
            "🎁 Tithes Before Lives": ["💸・offerings-box", "🎖️・blessed-boosters", "🌟・miracle-shoutouts"],
            "📚 Scripture & Sound": ["📺・holy-streams", "📖・blasphemous-books", "🎧・hymns-and-bangers"],
            "🔐 Disciple Sanctum": ["✨・divine-access", "👁️・behind-the-veil", "🛎️・tithe-support"],
            "⚖️ The Ministry of Mayhem": ["🗂️・papal-planning", "📢・divine-decrees", "⛔・banishment-records"]
        }

        for category_name, channels in structure.items():
            cat = discord.utils.get(guild.categories, name=category_name)
            if not cat:
                cat = await guild.create_category(category_name)
                print(f"📂 Created category: {category_name}")

            for ch in channels:
                if discord.utils.get(guild.channels, name=ch):
                    print(f"⏩ Skipped existing channel: {ch}")
                    continue
                try:
                    if ch == "📖・messiahs-commandments":
                        await guild.create_text_channel(ch, category=cat)
                    elif "prayer-circle" in ch or "squad-up" in ch:
                        await guild.create_voice_channel(ch, category=cat)
                    elif ch.startswith("📺") or ch.startswith("📖") or ch.startswith("🎧"):
                        try:
                            await guild.create_forum_channel(ch, category=cat)
                        except:
                            await guild.create_text_channel(ch, category=cat)
                    else:
                        await guild.create_text_channel(ch, category=cat)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    print(f"❌ Failed to create {ch}: {e}")

        await ctx.send("🎉 Server setup complete. Only missing items were added.")

async def setup(bot):
    await bot.add_cog(BuildServer(bot))
