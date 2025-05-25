import discord
from discord.ext import commands
import json
from datetime import datetime
import io

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
            await self.bot.wait_for("message", check=check, timeout=30.0)
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
                except Exception as e:
                    print(f"❌ Failed to export role {role.name}: {e}")

        for channel in guild.channels:
            try:
                perms = {}
                for overwrite_target, overwrite in channel.overwrites.items():
                    if isinstance(overwrite_target, discord.Role):
                        perms[overwrite_target.name] = {
                            "view_channel": overwrite.view_channel,
                            "send_messages": overwrite.send_messages,
                            "manage_messages": overwrite.manage_messages,
                            "read_message_history": overwrite.read_message_history
                        }

                backup["channels"].append({
                    "name": channel.name,
                    "type": str(channel.type),
                    "category": channel.category.name if channel.category else None,
                    "permissions": perms
                })
            except Exception as e:
                print(f"❌ Failed to export channel {channel.name}: {e}")

        filename = f"server_backup_{guild.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.json"
        backup_bytes = json.dumps(backup, indent=2).encode("utf-8")
        backup_file = discord.File(io.BytesIO(backup_bytes), filename=filename)

        # Find the channel named "papal-planning" or use current if not found
        upload_channel = discord.utils.get(guild.text_channels, name="papal-planning") or ctx.channel
        await upload_channel.send(content="🧾 Server backup file:", file=backup_file)

        await ctx.send("🧨 Now resetting roles and channels...")

        core_roles = ["@everyone", "Anointed", "Disciple", "Priest", "Messiah", "Pilgrims"]
        core_channels = ["messiahs-commandments"]

        for channel in guild.channels:
            if channel.name not in core_channels:
                try:
                    await channel.delete()
                except Exception as e:
                    print(f"❌ Failed to delete channel {channel.name}: {e}")

        for category in guild.categories:
            try:
                await category.delete()
            except Exception as e:
                print(f"❌ Failed to delete category {category.name}: {e}")

        for role in guild.roles:
            if role.name not in core_roles and not role.managed and role != guild.default_role:
                try:
                    await role.delete()
                except Exception as e:
                    print(f"❌ Failed to delete role {role.name}: {e}")

        await ctx.send("✅ Server reset complete. Backup has been uploaded.")

async def setup(bot):
    await bot.add_cog(ResetServer(bot))
