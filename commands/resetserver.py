import discord
from discord.ext import commands
import json
from datetime import datetime
import os

class ResetServer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="resetserver")
    @commands.has_permissions(administrator=True)
    async def reset_server(self, ctx):
        confirm_msg = await ctx.send(
            "⚠️ **This will permanently delete channels and roles.**\n"
            "Before we do that, I’ll export a backup of your current structure.\n"
            "Type `CONFIRM` within 30 seconds to proceed."
        )

        def check(m):
            return m.author == ctx.author and m.content.strip().upper() == "CONFIRM"

        try:
            confirmation = await self.bot.wait_for("message", check=check, timeout=30.0)
        except Exception:
            await ctx.send("❌ Reset cancelled.")
            return

        await ctx.send("📦 Exporting server structure before reset...")

        guild = ctx.guild
        backup = {
            "roles": [],
            "channels": [],
            "timestamp": datetime.utcnow().isoformat()
        }

        for role in guild.roles:
            if not role.managed:
                try:
                    backup["roles"].append({
                        "name": role.name,
                        "color": role.color.to_rgb(),
                        "permissions": role.permissions.value,
                        "position": role.position
                    })
                    print(f"✅ Exported role: {role.name}")
                except Exception as e:
                    print(f"❌ Failed to export role {role.name}: {e}")

        for channel in guild.channels:
            try:
                backup["channels"].append({
                    "name": channel.name,
                    "type": str(channel.type),
                    "category": channel.category.name if channel.category else None
                })
                print(f"✅ Exported channel: {channel.name}")
            except Exception as e:
                print(f"❌ Failed to export channel {channel.name}: {e}")

        filename = f"server_backup_{guild.id}.json"
        filepath = f"/mnt/data/{filename}"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(backup, f, indent=2)
            await ctx.send(f"✅ Backup saved: `{filename}`")
        except Exception as e:
            await ctx.send(f"❌ Failed to save backup: {e}")
            return

        core_roles = ["@everyone", "Anointed", "Disciple", "Priest", "Messiah", "Pilgrims"]
        core_channels = ["messiahs-commandments"]

        for channel in guild.channels:
            if channel.name not in core_channels:
                try:
                    await channel.delete()
                    print(f"🗑️ Deleted channel: {channel.name}")
                except Exception as e:
                    print(f"❌ Failed to delete channel {channel.name}: {e}")

        for category in guild.categories:
            try:
                await category.delete()
                print(f"🗑️ Deleted category: {category.name}")
            except Exception as e:
                print(f"❌ Failed to delete category {category.name}: {e}")

        for role in guild.roles:
            if role.name not in core_roles and not role.managed and role != guild.default_role:
                try:
                    await role.delete()
                    print(f"🗑️ Deleted role: {role.name}")
                except Exception as e:
                    print(f"❌ Failed to delete role {role.name}: {e}")

        await ctx.send("✅ Server reset complete. Backup file is ready for download.")

async def setup(bot):
    await bot.add_cog(ResetServer(bot))
