import discord
from discord.ext import commands
import json
import os

class RestoreBackup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="restorebackup")
    @commands.has_permissions(administrator=True)
    async def restore_backup(self, ctx, filename: str):
        filepath = f"/mnt/data/{filename}"
        if not os.path.exists(filepath):
            await ctx.send(f"❌ File `{filename}` not found.")
            return

        with open(filepath, "r", encoding="utf-8") as f:
            backup = json.load(f)

        guild = ctx.guild
        await ctx.send("📥 Restoring roles...")

        role_map = {}
        for role_data in sorted(backup["roles"], key=lambda r: r["position"]):
            name = role_data["name"]
            existing = discord.utils.get(guild.roles, name=name)
            if not existing:
                color = discord.Color.from_rgb(*role_data["color"])
                perms = discord.Permissions(role_data["permissions"])
                new_role = await guild.create_role(name=name, colour=color, permissions=perms)
                role_map[name] = new_role
                print(f"✅ Restored role: {name}")
            else:
                role_map[name] = existing

        await ctx.send("📥 Restoring channels...")

        category_map = {}
        for ch in backup["channels"]:
            try:
                if ch["type"] == "ChannelType.category":
                    cat = await guild.create_category(ch["name"])
                    category_map[ch["name"]] = cat
                    print(f"📁 Created category: {ch['name']}")
            except Exception as e:
                print(f"❌ Failed to create category {ch['name']}: {e}")

        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.text" and ch["name"] != "messiahs-commandments":
                cat = category_map.get(ch["category"])
                await guild.create_text_channel(ch["name"], category=cat)
                print(f"💬 Created text channel: {ch['name']}")
            elif ch["type"] == "ChannelType.voice":
                cat = category_map.get(ch["category"])
                await guild.create_voice_channel(ch["name"], category=cat)
                print(f"🔊 Created voice channel: {ch['name']}")
            elif ch["type"] == "ChannelType.forum":
                cat = category_map.get(ch["category"])
                try:
                    await guild.create_forum_channel(ch["name"], category=cat)
                    print(f"🗂️ Created forum channel: {ch['name']}")
                except Exception as e:
                    await guild.create_text_channel(ch["name"], category=cat)
                    print(f"⚠️ Forum failed, created text instead: {ch['name']}")

        await ctx.send("✅ Backup restoration complete!")

async def setup(bot):
    await bot.add_cog(RestoreBackup(bot))
