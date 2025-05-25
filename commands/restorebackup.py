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

        await ctx.send("📥 Restoring from uploaded backup... (skipping existing items)")

        guild = ctx.guild
        role_map = {}

        # Rebuild roles (skip if already exists)
        for role_data in sorted(backup["roles"], key=lambda r: r["position"]):
            name = role_data["name"]
            existing = discord.utils.get(guild.roles, name=name)
            if not existing:
                color = discord.Color.from_rgb(*role_data["color"])
                perms = discord.Permissions(role_data["permissions"])
                new_role = await guild.create_role(name=name, colour=color, permissions=perms)
                role_map[name] = new_role
            else:
                role_map[name] = existing

        # Rebuild categories (skip if already exists)
        category_map = {}
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                if not discord.utils.get(guild.categories, name=ch["name"]):
                    cat = await guild.create_category(ch["name"])
                    category_map[ch["name"]] = cat
                else:
                    category_map[ch["name"]] = discord.utils.get(guild.categories, name=ch["name"])

        # Rebuild channels (skip if already exists)
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                continue
            if discord.utils.get(guild.channels, name=ch["name"]):
                print(f"⏩ Skipped existing channel: {ch['name']}")
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

        await ctx.send("✅ Restore complete! Only missing items were created.")

async def setup(bot):
    await bot.add_cog(RestoreBackup(bot))
