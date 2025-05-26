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
        log_lines = ["🛠️ Constructing The Vatican’t..."]

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
                    log_lines.append(f"🎨 Updated role: {role_name}")
                except:
                    log_lines.append(f"⚠️ Could not update role: {role_name}")
            else:
                await guild.create_role(name=role_name, colour=color)
                log_lines.append(f"✅ Created role: {role_name}")

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

        existing_categories = {cat.name: cat for cat in guild.categories}
        existing_channels = {ch.name: ch for ch in guild.channels}

        for category_name, channels in structure.items():
            category = existing_categories.get(category_name)
            if not category:
                category = await guild.create_category(category_name)
                existing_categories[category_name] = category
                log_lines.append(f"📂 Created category: {category_name}")

            for channel_name in channels:
                existing = existing_channels.get(channel_name)
                if existing:
                    if existing.category != category:
                        try:
                            await existing.edit(category=category)
                            log_lines.append(f"🔁 Moved {channel_name} → {category_name}")
                        except Exception as e:
                            log_lines.append(f"❌ Couldn't move {channel_name}: {e}")
                    else:
                        log_lines.append(f"⏩ Skipped (already correct): {channel_name}")
                    continue

                try:
                    if "prayer-circle" in channel_name or "squad-up" in channel_name:
                        await guild.create_voice_channel(channel_name, category=category)
                    elif channel_name.startswith("📺") or channel_name.startswith("📖") or channel_name.startswith("🎧"):
                        try:
                            await guild.create_forum_channel(channel_name, category=category)
                        except:
                            await guild.create_text_channel(channel_name, category=category)
                    else:
                        await guild.create_text_channel(channel_name, category=category)
                    log_lines.append(f"📥 Created channel: {channel_name}")
                    await asyncio.sleep(0.2)
                except Exception as e:
                    log_lines.append(f"❌ Failed to create {channel_name}: {e}")

        # Output logs to Discord
        max_lines = 25
        for i in range(0, len(log_lines), max_lines):
            chunk = log_lines[i:i + max_lines]
            await ctx.send("```" + "".join(chunk) + "```")

        await ctx.send("✅ The Vatican’t is fully anointed and ready to slay.")

async def setup(bot):
    await bot.add_cog(BuildServer(bot))
