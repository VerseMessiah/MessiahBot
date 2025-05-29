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

        await ctx.send("📥 Restoring backup... (skipping existing items)")

        guild = ctx.guild
        role_map = {}
        stats = {"roles": 0, "categories": 0, "channels": 0, "overwrites": 0}

        # Rebuild roles
        for role_data in sorted(backup["roles"], key=lambda r: r["position"]):
            name = role_data["name"]
            existing = discord.utils.get(guild.roles, name=name)
            if not existing:
                color = discord.Color.from_rgb(*role_data["color"])
                perms = discord.Permissions(role_data["permissions"])
                new_role = await guild.create_role(name=name, colour=color, permissions=perms)
                role_map[name] = new_role
                stats["roles"] += 1
            else:
                role_map[name] = existing

        # Rebuild categories
        category_map = {}
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                if not discord.utils.get(guild.categories, name=ch["name"]):
                    cat = await guild.create_category(ch["name"])
                    category_map[ch["name"]] = cat
                    stats["categories"] += 1
                else:
                    category_map[ch["name"]] = discord.utils.get(guild.categories, name=ch["name"])

        # Rebuild channels with permissions
        for ch in backup["channels"]:
            if ch["type"] == "ChannelType.category":
                continue
            if discord.utils.get(guild.channels, name=ch["name"]):
                continue

            cat = category_map.get(ch["category"])
            overwrites = discord.PermissionOverwrite()
            perms = {}

            # Build overwrites from stored data
            if "permissions" in ch:
                perms = {}
                for target_id, perm in ch["permissions"].items():
                    allow = discord.Permissions(perm["allow"])
                    deny = discord.Permissions(perm["deny"])
                    target = guild.get_role(int(target_id)) or guild.get_member(int(target_id))
                    if target:
                        perms[target] = discord.PermissionOverwrite.from_pair(allow, deny)
                        stats["overwrites"] += 1

            try:
                created_channel = None
                if ch["type"] == "ChannelType.text" and ch["name"] != "messiahs-commandments":
                    created_channel = await guild.create_text_channel(ch["name"], category=cat, overwrites=perms)
                elif ch["type"] == "ChannelType.voice":
                    created_channel = await guild.create_voice_channel(ch["name"], category=cat, overwrites=perms)
                elif ch["type"] == "ChannelType.forum":
                    try:
                        created_channel = await guild.create_forum_channel(ch["name"], category=cat, overwrites=perms)
                    except:
                        created_channel = await guild.create_text_channel(ch["name"], category=cat, overwrites=perms)
                if created_channel:
                    stats["channels"] += 1
            except Exception as e:
                print(f"❌ Failed to create channel {ch['name']}: {e}")

        await ctx.send(
            f"✅ Restore complete!
"
            f"Created: {stats['roles']} roles, {stats['categories']} categories, {stats['channels']} channels
"
            f"Applied {stats['overwrites']} permission overwrites."
        )

async def setup(bot):
    await bot.add_cog(RestoreBackup(bot))
