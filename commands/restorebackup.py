import discord
from discord.ext import commands
import json

class RestoreBackup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="restorebackup")
    @commands.has_permissions(administrator=True)
    async def restore_backup(self, ctx):
        if not ctx.message.attachments:
            await ctx.send("📎 Please upload a `.json` backup file along with this command.")
            return

        attachment = ctx.message.attachments[0]
        if not attachment.filename.endswith(".json"):
            await ctx.send("❌ That doesn't look like a JSON file.")
            return

        try:
            content = await attachment.read()
            backup = json.loads(content.decode("utf-8"))
        except Exception as e:
            await ctx.send(f"❌ Failed to read backup file: {e}")
            return

        await ctx.send("📥 Restoring from uploaded backup...")

        guild = ctx.guild
        role_map = {}

        # Rebuild roles
        for role_data in sorted(backup["roles"], key=lambda r: r["position"]):
            name = role_data["name"]
            color = discord.Color.from_rgb(*role_data["color"])
            perms = discord.Permissions(role_data["permissions"])
            existing = discord.utils.get(guild.roles, name=name)
            if not existing:
                new_role = await guild.create_role(name=name, colour=color, permissions=perms)
                role_map[name] = new_role
            else:
                role_map[name] = existing

        # Rebuild categories
        category_map = {}
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                cat = await guild.create_category(ch["name"])
                category_map[ch["name"]] = cat

        # Rebuild channels
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                continue
            cat = category_map.get(ch["category"])
            try:
                if ch["type"] == "ChannelType.text" and ch["name"] != "messiahs-commandments":
                    await guild.create_text_channel(ch["name"], category=cat)
                elif ch["type"] == "ChannelType.voice":
                    await guild.create_voice_channel(ch["name"], category=cat)
                elif ch["type"] == "ChannelType.forum":
                    try:
                        await guild.create_forum_channel(ch["name"], category=cat)
                    except:
                        await guild.create_text_channel(ch["name"], category=cat)
            except Exception as e:
                print(f"❌ Failed to create channel {ch['name']}: {e}")

        await ctx.send("✅ Restore complete!")

async def setup(bot):
    await bot.add_cog(RestoreBackup(bot))
