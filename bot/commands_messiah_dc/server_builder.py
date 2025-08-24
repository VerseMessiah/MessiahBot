# bot/commands_messiah_dc/server_builder.py
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional
import discord
from discord.ext import commands
from discord import app_commands

# Optional Postgres support (used if DATABASE_URL is set)
DATABASE_URL = os.getenv("DATABASE_URL")
_psyco_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psyco_ok = True
except Exception:
    _psyco_ok = False

def _load_layout_for_guild(guild_id: int):
    if _psyco_ok and DATABASE_URL:
        with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT payload FROM builder_layouts WHERE guild_id=%s ORDER BY version DESC LIMIT 1",
                    (str(guild_id),),
                )
                row = cur.fetchone()
                if row and row.get("payload"):
                    return row["payload"]
    # fallback to local file...

        
    # Fallback for local testing
    path = os.getenv("LOCAL_LATEST_CONFIG", "latest_config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def _norm(s: Optional[str]) -> str:
    return (s or "").strip()

def _hex_to_color(hex_str: Optional[str]) -> discord.Color:
    s = (hex_str or "").strip().lstrip("#")
    try:
        return discord.Color(int(s, 16))
    except Exception:
        return discord.Color.default()

def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    nl = name.lower()
    return next((r for r in guild.roles if r.name.lower() == nl), None)

def _find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    nl = name.lower()
    return next((c for c in guild.categories if c.name.lower() == nl), None)

def _find_text(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    nl = name.lower()
    return next((c for c in guild.text_channels if c.name.lower() == nl), None)

def _find_voice(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    nl = name.lower()
    return next((c for c in guild.voice_channels if c.name.lower() == nl), None)

def _find_forum(guild: discord.Guild, name: str) -> Optional[discord.ForumChannel]:
    # discord.py 2.5 has Guild.forums
    nl = name.lower()
    return next((c for c in guild.forums if c.name.lower() == nl), None)

class ServerBuilder(commands.Cog):
    """MessiahBot: build/update server from form JSON (roles/categories/channels)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="build_server", description="Messiah: Build server from latest saved layout")
    @app_commands.checks.has_permissions(administrator=True)
    async def build_server(self, interaction: discord.Interaction):
        await interaction.response.send_message("🏗️ Messiah starting **full build**…", ephemeral=True)
        layout = _load_layout_for_guild(interaction.guild.id)
        if not layout:
            await interaction.followup.send("❌ No layout found for this guild. Save one from the dashboard.", ephemeral=True)
            return
        await self._apply_layout(interaction.guild, layout, update_only=False)
        await interaction.followup.send("✅ Build complete.", ephemeral=True)

    @app_commands.command(name="update_server", description="Messiah: Update server to match latest saved layout")
    @app_commands.checks.has_permissions(administrator=True)
    async def update_server(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔧 Messiah applying **update**…", ephemeral=True)
        layout = _load_layout_for_guild(interaction.guild.id)
        if not layout:
            await interaction.followup.send("❌ No layout found for this guild. Save one from the dashboard.", ephemeral=True)
            return
        await self._apply_layout(interaction.guild, layout, update_only=True)
        await interaction.followup.send("✅ Update complete.", ephemeral=True)

    async def _apply_layout(self, guild: discord.Guild, layout: Dict[str, Any], update_only: bool):
        """
        layout = {
          "mode": "build"|"update",
          "roles": [{"name": str, "color": "#RRGGBB"}],
          "categories": [str, ...],
          "channels": [{"name": str, "type": "text|voice|forum", "category": str}, ...]
        }
        """
        logs: List[str] = []

        # ROLES
        for r in layout.get("roles", []):
            name = _norm(r.get("name"))
            if not name:
                continue
            color = _hex_to_color(r.get("color"))
            existing = _find_role(guild, name)
            if existing is None:
                try:
                    await guild.create_role(name=name, color=color, reason="MessiahBot builder")
                    logs.append(f"✅ Role created: **{name}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create role: **{name}**")
            else:
                try:
                    await existing.edit(colour=color, reason="MessiahBot update role color")
                    logs.append(f"🔄 Role updated color: **{name}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to edit role: **{name}**")

        # CATEGORIES
        cat_cache: Dict[str, discord.CategoryChannel] = {}
        for cname in layout.get("categories", []):
            cname_n = _norm(cname)
            if not cname_n:
                continue
            cat = _find_category(guild, cname_n)
            if cat is None:
                try:
                    cat = await guild.create_category(cname_n, reason="MessiahBot builder")
                    logs.append(f"✅ Category created: **{cname_n}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create category: **{cname_n}**")
            else:
                logs.append(f"⏭️ Category exists: **{cname_n}**")
            if cat:
                cat_cache[cname_n.lower()] = cat

        # CHANNELS
        for ch in layout.get("channels", []):
            chname = _norm(ch.get("name"))
            chtype = (_norm(ch.get("type")) or "text").lower()
            catname = _norm(ch.get("category"))
            if not chname:
                continue

            parent = None
            if catname:
                parent = _find_category(guild, catname) or cat_cache.get(catname.lower())
                if parent is None:
                    try:
                        parent = await guild.create_category(catname, reason="MessiahBot builder (parent for channel)")
                        logs.append(f"✅ Category created for parent: **{catname}**")
                        cat_cache[catname.lower()] = parent
                    except discord.Forbidden:
                        logs.append(f"❌ Missing permission to create category: **{catname}**")

            # find existing by type
            existing = None
            if chtype == "text":
                existing = _find_text(guild, chname)
            elif chtype == "voice":
                existing = _find_voice(guild, chname)
            elif chtype == "forum":
                existing = _find_forum(guild, chname)
            else:
                existing = _find_text(guild, chname)
                chtype = "text"

            if existing is None:
                try:
                    if chtype == "text":
                        await guild.create_text_channel(chname, category=parent, reason="MessiahBot builder")
                    elif chtype == "voice":
                        await guild.create_voice_channel(chname, category=parent, reason="MessiahBot builder")
                    elif chtype == "forum":
                        await guild.create_forum(name=chname, category=parent, reason="MessiahBot builder")
                    logs.append(f"✅ Channel created: **#{chname}** [{chtype}]{' → ' + parent.name if parent else ''}")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname}**")
            else:
                try:
                    need_parent_id = parent.id if parent else None
                    has_parent_id = existing.category.id if getattr(existing, "category", None) else None
                    if need_parent_id != has_parent_id:
                        await existing.edit(category=parent, reason="MessiahBot move to correct category")
                        logs.append(f"🔀 Moved **#{chname}** → **{parent.name if parent else 'no category'}**")
                    else:
                        logs.append(f"⏭️ Channel exists & placed: **#{chname}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to move channel: **{chname}**")

        # Optional: print summary in console (you can later post to an admin log channel)
        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))

async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))
