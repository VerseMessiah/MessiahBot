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
        await ctx.send("🛠️ Constructing The Vatican’t in all its divine glory...")

        role_colors = {
            "Anointed": discord.Color.from_rgb(173, 216, 230),
            "Disciple": discord.Color.from_rgb(146, 132, 246),
            "Holy Jester": discord.Color.from_rgb(255, 87, 187),
            "Priest": discord.Color.from_rgb(249, 200, 14),
            "Messiah": discord.Color.from_rgb(0, 96, 199),
            "Pilgrims": discord.Color.from_rgb(58, 58, 58)
        }

        for role_name, color in role_colors.items():
            existing_role = discord.utils.get(guild.roles, name=role_name)
            if existing_role:
                await existing_role.edit(colour=color)
                print(f"🎨 Updated role color: {role_name}")
            else:
                await guild.create_role(name=role_name, colour=color)
                print(f"✅ Created role: {role_name} with color {color}")

        await ctx.send("📁 Roles created or updated. Now setting up channels...")

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

        everyone = guild.default_role
        anointed = discord.utils.get(guild.roles, name="Anointed")
        disciple = discord.utils.get(guild.roles, name="Disciple")
        priest = discord.utils.get(guild.roles, name="Priest")
        messiah = discord.utils.get(guild.roles, name="Messiah")

        for category_name, channels in structure.items():
            try:
                cat = await guild.create_category(category_name)
                print(f"📂 Created category: {category_name}")
            except Exception as e:
                print(f"❌ Failed to create category {category_name}: {e}")
                continue

            for ch in channels:
                try:
                    if ch == "📖・messiahs-commandments":
                        await guild.create_text_channel(ch, category=cat)
                        print(f"📝 Created required text: {ch}")
                    elif "prayer-circle" in ch or "squad-up" in ch:
                        await guild.create_voice_channel(ch, category=cat)
                        print(f"🎙️ Created voice: {ch}")
                    elif ch.startswith("📺") or ch.startswith("📖") or ch.startswith("🎧"):
                        try:
                            await guild.create_forum_channel(ch, category=cat)
                            print(f"🗂️ Created forum: {ch}")
                        except Exception as e:
                            print(f"⚠️ Failed to create forum for {ch}: {e}")
                            await guild.create_text_channel(ch, category=cat)
                            print(f"📝 Fallback to text: {ch}")
                    else:
                        await guild.create_text_channel(ch, category=cat)
                        print(f"📝 Created text: {ch}")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"❌ Failed to create channel {ch}: {e}")

            try:
                if category_name == "⚖️ The Ministry of Mayhem":
                    await cat.set_permissions(everyone, view_channel=False)
                    if priest:
                        await cat.set_permissions(priest, view_channel=True)
                    if messiah:
                        await cat.set_permissions(messiah, view_channel=True)
                elif category_name == "📜 Sanctified Entry":
                    await cat.set_permissions(everyone, view_channel=True, send_messages=False)
                elif category_name == "🔐 Disciple Sanctum":
                    await cat.set_permissions(everyone, view_channel=False)
                    if disciple:
                        await cat.set_permissions(disciple, view_channel=True, send_messages=True)
                else:
                    await cat.set_permissions(everyone, view_channel=False)
                    if anointed:
                        await cat.set_permissions(anointed, view_channel=True, send_messages=True)
            except Exception as e:
                print(f"⚠️ Failed to set permissions for {category_name}: {e}")

        await ctx.send("🎉 All done. The Vatican’t is now fully anointed and ready to slay.")

async def setup(bot):
    await bot.add_cog(BuildServer(bot))