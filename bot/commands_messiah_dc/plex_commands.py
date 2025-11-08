import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import View, Button
from bot.plex_utils import get_library_names

class PlexAccess(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Roles that can auto-approve themselves
        self.auto_approve_roles = ["Messiah", "Priest", "Disciple", "Analog Apostle"]
        # Channel where mod requests go
        self.mod_channel_name = "📜mod-actions"
        # Role that Membarr watches
        self.plex_role_name = "Plex Access"

    @app_commands.command(name="requestaccess", description="Request access to the VerseMessiah Plex server")
    async def requestaccess(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        plex_role = discord.utils.get(guild.roles, name=self.plex_role_name)
        if not plex_role:
            await interaction.response.send_message(
                f"⚠️ The `{self.plex_role_name}` role doesn’t exist — please ask an admin to configure Membarr first.",
                ephemeral=True
            )
            return

        # already has access
        if plex_role in member.roles:
            await interaction.response.send_message(
                "✅ You already have Plex Access! Check your Plex invites or library.",
                ephemeral=True
            )
            return

        # if member has any auto-approve role
        if any(r.name in self.auto_approve_roles for r in member.roles):
            await member.add_roles(plex_role, reason="Auto-approved Plex access")
            await interaction.response.send_message(
                "🎬 Auto-approved! You now have Plex Access — check your Plex invites shortly.",
                ephemeral=True
            )
            return

        # otherwise require moderator approval
        mod_channel = discord.utils.get(guild.text_channels, name=self.mod_channel_name)
        if not mod_channel:
            await interaction.response.send_message(
                f"🪄 Request received! (No `{self.mod_channel_name}` channel found, please ping a mod.)",
                ephemeral=True
            )
            return

        # Create approval buttons
        class ApproveView(View):
            def __init__(self):
                super().__init__(timeout=None)
                self.value = None

            @discord.ui.button(label="Approve ✅", style=discord.ButtonStyle.success)
            async def approve(self, interaction_button: discord.Interaction, button: Button):
                await member.add_roles(plex_role, reason="Approved Plex access")
                await interaction_button.response.send_message(
                    f"✅ Approved and granted Plex Access to {member.mention}.", ephemeral=False)
                await mod_channel.send(f"🎟️ {member.mention} has been **approved** for Plex Access by {interaction_button.user.mention}.")
                self.value = True
                self.stop()

            @discord.ui.button(label="Deny ❌", style=discord.ButtonStyle.danger)
            async def deny(self, interaction_button: discord.Interaction, button: Button):
                await interaction_button.response.send_message(
                    f"❌ Denied Plex Access for {member.mention}.", ephemeral=False)
                await mod_channel.send(f"🚫 {member.mention}'s Plex Access request was **denied** by {interaction_button.user.mention}.")
                self.value = False
                self.stop()

        view = ApproveView()
        await mod_channel.send(
            f"🎟️ **Plex Access Request:** {member.mention} wants access.\n"
            f"Approve or Deny below ⬇️",
            view=view
        )
        await interaction.response.send_message(
            "📩 Your request has been sent to the moderators for approval!",
            ephemeral=True
        )

class PlexAccess(commands.Cog):
    async def plex_status(self, interaction: discord.Interaction):
        try:
            libs = get_library_names()
            await interaction.response.send_message(
                f"✅ Connected to VerseMessiah's Plex Server! Libraries: {', '.join(libs)}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to connect to Plex Server: {str(e)}",
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(PlexAccess(bot))
