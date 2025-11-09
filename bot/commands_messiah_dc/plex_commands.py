import os
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import xml.etree.ElementTree as ET

PLEX_TOKEN = os.getenv("PLEX_TOKEN")

class PlexCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    #
    # Utility: perform authenticated call through Plex Cloud
    #
    async def plex_get(self, session, url):
        headers = {"X-Plex-Token": PLEX_TOKEN}
        async with session.get(url, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status} from Plex: {url}")
            return await resp.text()

    #
    # /plexlist — list available Plex servers (via Plex Cloud)
    #
    @app_commands.command(name="plexlist", description="List available Plex servers on your Plex account")
    async def plexlist(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not PLEX_TOKEN:
            await interaction.followup.send("⚠️ Plex token not configured.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            xml_data = await self.plex_get(session, "https://plex.tv/pms/resources?includeHttps=1")
            root = ET.fromstring(xml_data)
            servers = [
                f"**{srv.get('name')}** ({srv.get('product')}) — {srv.get('address')}"
                for srv in root.findall(".//Device[@product='Plex Media Server']")
            ]

        embed = discord.Embed(title="🎬 Plex Servers", color=0xFFD700)
        embed.description = "\n".join(servers) if servers else "No Plex servers found."
        await interaction.followup.send(embed=embed, ephemeral=False)

    #
    # /plexrecent — show recently added media (movies/shows)
    #
    @app_commands.command(name="plexrecent", description="Show recently added media from your Plex server")
    async def plexrecent(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not PLEX_TOKEN:
            await interaction.followup.send("⚠️ Plex token not configured.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            # Step 1: Get your primary server connection
            xml_data = await self.plex_get(session, "https://plex.tv/pms/resources?includeHttps=1")
            root = ET.fromstring(xml_data)
            server = root.find(".//Device[@product='Plex Media Server']")
            if not server:
                await interaction.followup.send("❌ No Plex Media Server found.")
                return
            conn = server.find(".//Connection[@protocol='https']")
            base_url = conn.get("uri")

            # Step 2: Fetch recent items
            xml_recent = await self.plex_get(session, f"{base_url}/library/recentlyAdded")
            tree = ET.fromstring(xml_recent)

            items = []
            for video in tree.findall(".//Video")[:10]:
                title = video.get("title")
                media_type = video.get("type")
                thumb = video.get("thumb")
                items.append((title, media_type, f"{base_url}{thumb}?X-Plex-Token={PLEX_TOKEN}" if thumb else None))

        embed = discord.Embed(title="🆕 Recently Added", color=0x00BFFF)
        for title, media_type, thumb in items:
            embed.add_field(name=title, value=media_type.title(), inline=False)
        if items and items[0][2]:
            embed.set_thumbnail(url=items[0][2])
        await interaction.followup.send(embed=embed, ephemeral=False)

    #
    # /plexsearch — search titles in your Plex library
    #
    @app_commands.command(name="plexsearch", description="Search your Plex library for a movie or show title")
    async def plexsearch(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(ephemeral=True)
        if not PLEX_TOKEN:
            await interaction.followup.send("⚠️ Plex token not configured.", ephemeral=True)
            return

        async with aiohttp.ClientSession() as session:
            # Find the server
            xml_data = await self.plex_get(session, "https://plex.tv/pms/resources?includeHttps=1")
            root = ET.fromstring(xml_data)
            server = root.find(".//Device[@product='Plex Media Server']")
            if not server:
                await interaction.followup.send("❌ No Plex Media Server found.")
                return
            conn = server.find(".//Connection[@protocol='https']")
            base_url = conn.get("uri")

            # Perform search
            xml_search = await self.plex_get(session, f"{base_url}/search?query={query}")
            tree = ET.fromstring(xml_search)

            results = []
            for video in tree.findall(".//Video")[:10]:
                title = video.get("title")
                media_type = video.get("type")
                thumb = video.get("thumb")
                results.append((title, media_type, f"{base_url}{thumb}?X-Plex-Token={PLEX_TOKEN}" if thumb else None))

        embed = discord.Embed(title=f"🔍 Plex Search: {query}", color=0x9B59B6)
        if not results:
            embed.description = "No matches found."
        else:
            for title, media_type, thumb in results:
                embed.add_field(name=title, value=media_type.title(), inline=False)
            if results[0][2]:
                embed.set_thumbnail(url=results[0][2])

        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot):
    await bot.add_cog(PlexCommands(bot))
