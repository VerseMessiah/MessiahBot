# bot/commands_messiah_dc/server_builder.py
from __future__ import annotations
import os, json, asyncio
from typing import Dict, Any, List, Optional, Tuple
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp

# Optional Postgres support (used if DATABASE_URL is set)
DATABASE_URL = os.getenv("DATABASE_URL")
_psyco_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psyco_ok = True
except Exception:
    _psyco_ok = False


# ---------- small progress helper ----------

class Progress:
    def __init__(self, interaction: discord.Interaction, prefix: str = "🧱 Messiah: "):
        self.inter = interaction
        self.prefix = prefix
        self._last = ""
        self._lock = asyncio.Lock()

    async def set(self, msg: str):
        if msg == self._last:
            return
        async with self._lock:
            self._last = msg
            try:
                await self.inter.edit_original_response(content=f"{self.prefix}{msg}")
            except Exception:
                pass


# ---------- DB / layout helpers ----------

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

    path = os.getenv("LOCAL_LATEST_CONFIG", "latest_config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()


def _hex_to_color(hex_str: Optional[str]) -> discord.Color:
    s = (hex_str or "").strip().lstrip("#")
    try:
        return discord.Color(int(s, 16))
    except Exception:
        return discord.Color.default()


# ---------- finders ----------

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
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    nl = name.lower()
    return next((c for c in forums if c.name.lower() == nl), None)

def _find_stage(guild: discord.Guild, name: str) -> Optional[discord.StageChannel]:
    try:
        stages = list(getattr(guild, "stage_channels", []))
    except Exception:
        stages = []
    nl = name.lower()
    return next((c for c in stages if getattr(c, "name", "").lower() == nl), None)


# ---------- permissions / overwrites ----------

def _role_perms_from_flags(flags: Dict[str, bool]) -> discord.Permissions:
    p = discord.Permissions.none()
    if flags.get('admin'):             p.administrator = True
    if flags.get('manage_channels'):   p.manage_channels = True
    if flags.get('manage_roles'):      p.manage_roles = True
    if flags.get('view_channel'):      p.view_channel = True
    if flags.get('send_messages'):     p.send_messages = True
    if flags.get('connect'):           p.connect = True
    if flags.get('speak'):             p.speak = True
    return p


def _build_overwrites(guild: discord.Guild, ow_spec: Dict[str, Dict[str, str]]) -> Dict[discord.Role, discord.PermissionOverwrite]:
    out: Dict[discord.Role, discord.PermissionOverwrite] = {}
    if not isinstance(ow_spec, dict):
        return out

    def setp(ow: discord.PermissionOverwrite, attr: str, val: str):
        if val == "allow": setattr(ow, attr, True)
        elif val == "deny": setattr(ow, attr, False)
        else: setattr(ow, attr, None)

    for role_name, perms in ow_spec.items():
        role = _find_role(guild, role_name)
        if not role or not isinstance(perms, dict):
            continue
        ow = discord.PermissionOverwrite()
        setp(ow, "view_channel",   perms.get("view", "inherit"))
        setp(ow, "send_messages",  perms.get("send", "inherit"))
        setp(ow, "connect",        perms.get("connect", "inherit"))
        setp(ow, "speak",          perms.get("speak", "inherit"))
        setp(ow, "manage_channels",perms.get("manage_channels", "inherit"))
        setp(ow, "manage_roles",   perms.get("manage_roles", "inherit"))
        out[role] = ow
    return out


# ---------- snapshot (discord.py and REST fallback) ----------

def _safe_pos(obj, default=0):
    try:
        v = getattr(obj, "position", default)
        return v if isinstance(v, int) else default
    except Exception:
        return default

def _snapshot_guild(guild: discord.Guild) -> Dict[str, Any]:
    # ... same as your existing snapshot code ...
    # trimmed here for brevity
    # (keep the same body from your current file)
    return {
        "mode": "update",
        "roles": [],
        "categories": [],
        "channels": [],
        "prune": {"roles": False, "categories": False, "channels": False},
        "renames": {"roles": [], "categories": [], "channels": []},
        "community": {"enable_on_build": False, "settings": {}}
    }

async def _snapshot_guild_via_rest(guild: discord.Guild) -> Dict[str, Any]:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set for REST snapshot fallback")

    base = "https://discord.com/api/v10"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "MessiahBotSnapshot/1.0",
    }

    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{base}/guilds/{guild.id}/roles", headers=headers) as resp:
            roles_json = await resp.json()
        async with sess.get(f"{base}/guilds/{guild.id}/channels", headers=headers) as resp:
            chans_json = await resp.json()

    roles = []
    for r in roles_json:
        if r.get("managed") or r.get("name") == "@everyone":
            continue
        color_int = int(r.get("color") or 0)
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    categories = [c.get("name", "") for c in chans_json if c.get("type") == 4 and c.get("name")]
    cat_map = {c["id"]: c.get("name", "") for c in chans_json if c.get("type") == 4}

    def pos(x): return int(x.get("position", 0) or 0)

    channels = []
    for ch in sorted(chans_json, key=pos):
        t = ch.get("type")
        name = ch.get("name") or ""
        parent = cat_map.get(ch.get("parent_id"), "")
        if t == 0:
            channels.append({"name": name, "type": "text", "category": parent, "options": {}})
        elif t == 5:
            channels.append({"name": name, "type": "announcement", "category": parent, "options": {}})
        elif t == 2:
            channels.append({"name": name, "type": "voice", "category": parent, "options": {}})
        elif t == 13:
            channels.append({"name": name, "type": "stage", "category": parent, "options": {}})
        elif t == 15:
            channels.append({"name": name, "type": "forum", "category": parent, "options": {}})

    return {
        "mode": "update",
        "roles": roles,
        "categories": categories,
        "channels": channels,
        "prune": {"roles": False, "categories": False, "channels": False},
        "renames": {"roles": [], "categories": [], "channels": []},
        "community": {"enable_on_build": False, "settings": {}}
    }


# ---------- main cog ----------

class ServerBuilder(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="snapshot_layout", description="Messiah: Save current server structure as a new layout version")
    async def snapshot_layout(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not (_psyco_ok and DATABASE_URL):
            await interaction.followup.send("❌ Database not configured", ephemeral=True)
            return
        try:
            layout = _snapshot_guild(interaction.guild)
        except Exception as e1:
            print(f"[Messiah snapshot] failed: {e1}, falling back to REST")
            layout = await _snapshot_guild_via_rest(interaction.guild)
        with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(version),0)+1 AS v FROM builder_layouts WHERE guild_id=%s",
                    (str(interaction.guild.id),),
                )
                ver = int((cur.fetchone() or {}).get("v", 1))
                cur.execute(
                    "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s,%s,%s::jsonb)",
                    (str(interaction.guild.id), ver, json.dumps(layout)),
                )
        await interaction.followup.send(
            f"✅ Saved layout snapshot as version {ver}. Open the dashboard and click **Load Snapshot** to edit.",
            ephemeral=True
        )