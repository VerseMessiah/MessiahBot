# bot/commands_messiah_dc/server_builder.py
from __future__ import annotations
import os, json, asyncio
from typing import Dict, Any, List, Optional, Tuple
import discord
from discord.ext import commands
from discord import app_commands
import time
import requests, time as _t

# --- Tunables / safety knobs ---
SNAPSHOT_COOLDOWN_SEC = int(os.getenv("SNAPSHOT_COOLDOWN_SEC", "90"))
# Bumped default delay to reduce rate spikes during large updates
APPLY_EDIT_DELAY_SEC = float(os.getenv("APPLY_EDIT_DELAY_SEC", "0.8"))
# NEW: allow turning REST fallback completely off (default OFF)
ALLOW_REST_SNAPSHOT = os.getenv("ALLOW_REST_SNAPSHOT", "0") == "1"

_last_rest_snapshot_ts = 0

# CHANGE: small, consistent delay used after every write to Discord to avoid CF/Discord bursts
async def _throttle():
    await asyncio.sleep(APPLY_EDIT_DELAY_SEC)

# Single session for HTTP calls
sess = requests.Session()
sess.headers.update({
    "User-Agent": "MessiahBot/1.0 (+server_builder.py)"
})

# CHANGE: _get now accepts headers (no invisible global),
#         retries 3x w/ exponential backoff and detects CF 1015 HTML pages even on 200/403
def _get(url: str, headers: Dict[str, str]):
    back = 1.0
    for _ in range(3):
        r = sess.get(url, headers=headers, timeout=20)
        # CF/Discord rate/ban pages can be HTML even with 200/403
        body = r.text or ""
        if r.status_code in (429, 500, 502, 503, 504) or "temporarily from accessing" in body or "error-1015" in body:
            _t.sleep(back); back = min(back * 2, 8.0)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r

# --- Config / DB & Token ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # used for REST snapshot fallback

_psyco_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psyco_ok = True
except Exception:
    _psyco_ok = False


# ---------- small progress helper ----------
class Progress:
    """Throttled progress editor for the ephemeral 'thinking' message."""
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
#
# NOTE:
# builder_layouts now has a `type` column:
#   - 'snapshot' rows are saved via the dashboard or /snapshot_layout
#   - exactly one 'active' row (if present) is what build/update_server will apply
# This helper always prefers the active row, falling back to the latest snapshot.
def _load_layout_for_guild(guild_id: int):
    """Load the latest saved layout for this guild from DB, or local file as fallback."""
    if _psyco_ok and DATABASE_URL:
        with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT payload
                    FROM builder_layouts
                    WHERE guild_id=%s
                    ORDER BY
                      CASE WHEN type = 'active' THEN 0 ELSE 1 END,
                      version DESC
                    LIMIT 1
                    """,
                    (str(guild_id),),
                )
                row = cur.fetchone()
                if row and row.get("payload"):
                    return row["payload"]

    # Local fallback for dev
    path = os.getenv("LOCAL_LATEST_CONFIG", "latest_config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _norm(name: Optional[str]) -> str:
    return (name or "").strip()


def _hex_to_color(hex_str: Optional[str]) -> discord.Color:
    s = (hex_str or "").strip().lstrip("#")
    try:
        return discord.Color(int(s, 16))
    except Exception:
        return discord.Color.default()


# ---------- finders ----------
def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    """Find a role by exact name (case‑sensitive)."""
    return next((r for r in guild.roles if r.name == name), None)

def _find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    """Find a category by exact name (case‑sensitive)."""
    return next((c for c in guild.categories if c.name == name), None)

def _find_text(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    """Find a text (or announcement/news) channel by exact name (case‑sensitive).
    Announcement/news channels are subclasses of TextChannel and are included in guild.text_channels.
    """
    try:
        text_channels = list(guild.text_channels)
    except Exception:
        text_channels = []
    return next((c for c in text_channels if c.name == name), None)

def _find_voice(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    """Find a voice or stage channel by exact name (case‑sensitive).
    Stage channels are subclasses of VoiceChannel and are included in guild.voice_channels.
    """
    try:
        voice_channels = list(guild.voice_channels)
    except Exception:
        voice_channels = []
    return next((c for c in voice_channels if c.name == name), None)

def _find_forum(guild: discord.Guild, name: str) -> Optional[discord.ForumChannel]:
    """Find a forum channel by exact name (case‑sensitive)."""
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    return next((c for c in forums if c.name == name), None)


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
    """
    ow_spec = {
      "Role Name": {
        "view":"inherit|allow|deny",
        "send":"...", "connect":"...", "speak":"...",
        "manage_channels":"...", "manage_roles":"..."
      }
    }
    """
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


# ---------- snapshot helpers ----------
def _safe_pos(obj, default=0):
    try:
        v = getattr(obj, "position", default)
        return v if isinstance(v, int) else default
    except Exception:
        return default

def _snapshot_guild_discordpy(guild: discord.Guild) -> Dict[str, Any]:
    """Primary: use discord.py live objects for a rich snapshot.
    Emits nested categories with per-channel positions to preserve order.
    Skips any categories with blank/whitespace names.
    """
    roles: List[Dict[str, Any]] = []

    # Roles (skip @everyone + managed)
    for r in sorted(getattr(guild, "roles", []), key=lambda x: _safe_pos(x), reverse=True):
        if r.is_default() or r.managed:
            continue
        color_val = getattr(getattr(r, "colour", None), "value", 0) or 0
        perms_dict = {
            "admin": r.permissions.administrator,
            "manage_channels": r.permissions.manage_channels,
            "manage_roles": r.permissions.manage_roles,
            "view_channel": r.permissions.view_channel,
            "send_messages": r.permissions.send_messages,
            "connect": r.permissions.connect,
            "speak": r.permissions.speak,
        }
        roles.append({
            "name": r.name,
            "color": f"#{int(color_val):06x}",
            "perms": perms_dict
        })

    # Build nested categories with channel lists and explicit positions
    categories_payload: List[Dict[str, Any]] = []

    # Helper to detect announcement/text
    def _is_announcement(ch: discord.TextChannel) -> bool:
        try:
            if hasattr(ch, "is_news"):
                return bool(ch.is_news())
            elif hasattr(discord, "ChannelType"):
                return (getattr(ch, "type", None) == getattr(discord.ChannelType, "news", object()))
        except Exception:
            return False
        return False

    # Categories in display order (preserve API order)
    categories_sorted: List[discord.CategoryChannel] = list(getattr(guild, "categories", []))

    for cat in categories_sorted:
        name = (getattr(cat, "name", "") or "")
        if not name.strip():
            # Skip ghost/blank categories
            continue
        # Channels in category in display order (preserve API order)
        chans_sorted = list(getattr(cat, "channels", []))
        ch_items: List[Dict[str, Any]] = []

        for ch in chans_sorted:
            # Normalize type string
            ctype = "text"
            if hasattr(ch, "type"):
                if str(ch.type) == "ChannelType.voice":
                    ctype = "voice"
                elif str(ch.type) == "ChannelType.forum":
                    ctype = "forum"
                elif str(ch.type) == "ChannelType.stage_voice":
                    ctype = "stage"
                else:
                    ctype = "announcement" if _is_announcement(ch) else "text"

            # Channel options
            options = {}
            try:
                if hasattr(ch, "topic") and ch.topic:
                    options["topic"] = ch.topic
            except Exception:
                pass
            try:
                if hasattr(ch, "nsfw"):
                    options["nsfw"] = bool(ch.nsfw)
            except Exception:
                pass
            try:
                if hasattr(ch, "slowmode_delay"):
                    options["slowmode"] = int(ch.slowmode_delay or 0)
            except Exception:
                pass

            ch_items.append({
                "name": ch.name,
                "type": ctype,
                "position": _safe_pos(ch, 0),
                "options": options,
                # NOTE: We do not include overwrites unless the dashboard toggle is used to send them back.
                # "overwrites": {},
            })

        categories_payload.append({
            "name": name,
            "position": _safe_pos(cat, 0),
            "channels": ch_items,
            # Category overwrites omitted by default to avoid accidental wipes
            # "overwrites": {}
        })

    # Also include uncategorized channels if needed for UI (optional; kept empty here).
    # The current applier handles ordering within categories from the nested structure.
    return {
        "mode": "update",
        "roles": roles,
        "categories": categories_payload,
        "channels": [],  # leave flat list empty when nested is present to avoid duplication
        "prune": {"roles": False, "categories": False, "channels": False},
        "renames": {"roles": [], "categories": [], "channels": []},
        "community": {"enable_on_build": False, "settings": {}}
    }

def _snapshot_guild_rest(guild_id: int, token: Optional[str]) -> Dict[str, Any]:
    """Fallback: use REST API and emit nested categories with per-channel positions.
    Skips categories whose names are blank/whitespace.
    """
    # CHANGE: fixed token/base/headers scoping + cooldown logic order
    global _last_rest_snapshot_ts
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN not set; cannot run REST snapshot fallback.")
    if not ALLOW_REST_SNAPSHOT:
        raise RuntimeError("REST snapshot disabled (set ALLOW_REST_SNAPSHOT=1 to enable).")

    base = "https://discord.com/api/v10"
    headers = {"Authorization": f"Bot {token}"}

    now = time.time()
    if now - _last_rest_snapshot_ts < SNAPSHOT_COOLDOWN_SEC:
        raise RuntimeError(f"REST snapshot cooling down ({SNAPSHOT_COOLDOWN_SEC}s). Try again shortly.")

    # Roles (CHANGE: use resilient _get)
    r_roles = _get(f"{base}/guilds/{guild_id}/roles", headers)
    roles_json = r_roles.json()

    roles: List[Dict[str, Any]] = []
    for r in roles_json:
        if r.get("managed") or r.get("name") == "@everyone":
            continue
        color_int = int(r.get("color") or 0)
        perms_int = int(r.get("permissions") or 0)
        perms = discord.Permissions(perms_int)
        roles.append({
            "name": r.get("name",""),
            "color": f"#{color_int:06x}",
            "perms": {
                "admin": perms.administrator,
                "manage_channels": perms.manage_channels,
                "manage_roles": perms.manage_roles,
                "view_channel": perms.view_channel,
                "send_messages": perms.send_messages,
                "connect": perms.connect,
                "speak": perms.speak,
            }
        })

    # Channels (CHANGE: use resilient _get)
    r_channels = _get(f"{base}/guilds/{guild_id}/channels", headers)
    ch_json = r_channels.json()

    # Separate categories and non-categories
    cats = [c for c in ch_json if int(c.get("type", 0)) == 4]
    non_cats = [c for c in ch_json if int(c.get("type", 0)) != 4]

    # Build category map: id -> {name, position}
    cat_map: Dict[str, Dict[str, Any]] = {}
    for c in cats:
        nm = (c.get("name") or "")
        if not nm.strip():
            # Skip ghost/blank categories
            continue
        cat_map[str(c["id"])] = {
            "name": nm,
            "position": int(c.get("position", 0))
        }

    # Helper for channel type names
    def ch_type_name(t: int) -> str:
        # 0 text, 2 voice, 4 category, 5 news, 13 stage, 15 forum
        return {
            0: "text",
            5: "announcement",
            2: "voice",
            13: "stage",
            15: "forum"
        }.get(t, "text")

    # Bucket channels under categories
    cat_channels: Dict[str, List[Dict[str, Any]]] = {cid: [] for cid in cat_map.keys()}
    for ch in non_cats:
        t = int(ch.get("type", 0))
        parent_id = ch.get("parent_id")
        # Only include channels that have a valid parent category (skip uncategorized here)
        if not parent_id or str(parent_id) not in cat_map:
            continue

        options = {}
        if t in (0, 5):  # text/announcement supports topic, nsfw, slowmode
            options = {
                "topic": ch.get("topic") or "",
                "nsfw": bool(ch.get("nsfw", False)),
                "slowmode": int(ch.get("rate_limit_per_user") or 0),
            }

        cat_channels[str(parent_id)].append({
            "name": ch.get("name", ""),
            "type": ch_type_name(t),
            "position": int(ch.get("position", 0) or 0),
            "options": options
        })

    # Preserve Discord's REST API order (already in display order)
    # Do not resort here, just trust the API response
    # for cid, arr in cat_channels.items():
    #     arr.sort(key=lambda x: int(x.get("position", 0)))

    # Build nested categories payload preserving API order (do not re-sort)
    categories_payload: List[Dict[str, Any]] = []
    for cid, meta in cat_map.items():  # preserve API order
        categories_payload.append({
            "name": meta["name"],
            "position": int(meta["position"]),
            "channels": cat_channels.get(cid, [])
        })

    # CHANGE: record last REST snapshot moment only on success
    _last_rest_snapshot_ts = now
    return {
        "mode": "update",
        "roles": roles,
        "categories": categories_payload,
        "channels": [],  # leave flat list empty when nested is present to avoid duplication
        "prune": {"roles": False, "categories": False, "channels": False},
        "renames": {"roles": [], "categories": [], "channels": []},
        "community": {"enable_on_build": False, "settings": {}}
    }

def _snapshot_guild_best(guild: discord.Guild) -> Dict[str, Any]:
    """Try discord.py snapshot first; on error or empty result, fall back to REST."""
    try:
        dp = _snapshot_guild_discordpy(guild)
        # If discord.py returns nothing meaningful (some shards/permissions issues), handle toggle
        if (len(dp.get("roles", [])) == 0 and
            len(dp.get("categories", [])) == 0 and
            len(dp.get("channels", [])) == 0):
            if not ALLOW_REST_SNAPSHOT:
                raise RuntimeError("discord.py snapshot empty and REST fallback is disabled.")
            raise RuntimeError("discord.py snapshot returned empty; using REST fallback.")
        print(f"[Messiah snapshot] discord.py OK: roles={len(dp['roles'])} cats={len(dp['categories'])} chans={len(dp['channels'])}")
        return dp
    except Exception as e:
        if not ALLOW_REST_SNAPSHOT:
            # Surface the error to callers when REST is disabled
            raise
        print(f"[Messiah snapshot] discord.py failed: {e} -> falling back to REST")
        rest = _snapshot_guild_rest(guild.id, DISCORD_BOT_TOKEN)
        print(f"[Messiah snapshot] REST OK: roles={len(rest['roles'])} cats={len(rest['categories'])} chans={len(rest['channels'])}")
        return rest


# ---------- community settings ----------
async def _apply_community(guild: discord.Guild, community_payload: Dict[str, Any], is_build: bool):
    if not community_payload:
        return
    enable_on_build = bool(community_payload.get("enable_on_build"))
    settings = community_payload.get("settings") or {}

    if is_build and enable_on_build:
        try:
            await guild.edit(community=True)
            # CHANGE: throttle after write
            await _throttle()
        except Exception as e:
            print(f"[Messiah] community enable failed: {e}")

    try:
        features = getattr(guild, "features", [])
        is_community = ("COMMUNITY" in features) or getattr(guild, "community", False)
    except Exception:
        is_community = False
    if not is_community:
        return

    async def ensure_text_channel(name: Optional[str]):
        nm = _norm(name)
        if not nm:
            return None
        ch = _find_text(guild, nm)
        if ch:
            return ch
        try:
            ch = await guild.create_text_channel(nm, reason="MessiahBot community channel")
            # CHANGE: throttle after create
            await _throttle()
            return ch
        except Exception:
            return None

    rules_ch = await ensure_text_channel(settings.get("rules_channel"))
    updates_ch = await ensure_text_channel(settings.get("updates_channel"))

    ver_map = {
        "none": discord.VerificationLevel.none,
        "low": discord.VerificationLevel.low,
        "medium": discord.VerificationLevel.medium,
        "high": discord.VerificationLevel.high,
        "very_high": discord.VerificationLevel.very_high,
    }
    notif_map = {
        "all_messages": discord.NotificationLevel.all_messages,
        "only_mentions": discord.NotificationLevel.only_mentions,
    }
    exp_map = {
        "disabled": discord.ContentFilter.disabled,
        "members_without_roles": discord.ContentFilter.no_role,
        "all_members": discord.ContentFilter.all_members,
    }

    kwargs = {}
    if settings.get("verification") in ver_map:
        kwargs["verification_level"] = ver_map[settings["verification"]]
    if settings.get("notifications") in notif_map:
        kwargs["default_notifications"] = notif_map[settings["notifications"]]
    if settings.get("explicit_filter") in exp_map:
        kwargs["explicit_content_filter"] = exp_map[settings["explicit_filter"]]
    if rules_ch:
        kwargs["rules_channel"] = rules_ch
    if updates_ch:
        kwargs["public_updates_channel"] = updates_ch

    if kwargs:
        try:
            await guild.edit(**kwargs)
            # CHANGE: throttle after write
            await _throttle()
        except Exception as e:
            print(f"[Messiah] community settings edit failed: {e}")


# ---------- renames ----------
async def _apply_role_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    by_name = { _norm(r.name): r for r in guild.roles }
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst:
            continue
        role = by_name.get(src)
        if role and not role.managed and not role.is_default():
            try:
                await role.edit(name=dst, reason="Messiah rename (layout)")
                # CHANGE: throttle after write
                await _throttle()
            except Exception as e:
                print(f"[Messiah] role rename failed {role.name} -> {dst}: {e}")

async def _apply_category_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    by_name = { _norm(c.name): c for c in guild.categories }
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst:
            continue
        cat = by_name.get(src)
        if cat:
            try:
                await cat.edit(name=dst, reason="Messiah rename (layout)")
                # CHANGE: throttle after write
                await _throttle()
            except Exception as e:
                print(f"[Messiah] category rename failed {cat.name} -> {dst}: {e}")

async def _apply_channel_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    all_chans: List[discord.abc.GuildChannel] = list(guild.text_channels) + list(guild.voice_channels)
    try:
        all_chans += list(guild.forums)
    except Exception:
        pass
    by_name = { _norm(c.name): c for c in all_chans }
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst:
            continue
        ch = by_name.get(src)
        if ch:
            try:
                await ch.edit(name=dst, reason="Messiah rename (layout)")
                # CHANGE: throttle after write
                await _throttle()
            except Exception as e:
                print(f"[Messiah] channel rename failed {ch.name} -> {dst}: {e}")


# ---------- prune ----------
async def _prune_roles(guild: discord.Guild, desired_names: set[str]):
    for r in guild.roles:
        if r.is_default() or r.managed:
            continue
        if _norm(r.name) not in desired_names:
            try:
                await r.delete(reason="Messiah prune (not in layout)")
                # CHANGE: throttle after delete
                await _throttle()
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _prune_categories(guild: discord.Guild, desired_names: set[str]):
    for c in guild.categories:
        if _norm(c.name) not in desired_names:
            if len(c.channels) == 0:
                try:
                    await c.delete(reason="Messiah prune (not in layout)")
                    # CHANGE: throttle after delete
                    await _throttle()
                except Exception as e:
                    print(f"[Messiah] category delete failed {c.name}: {e}")

async def _prune_channels(guild: discord.Guild, desired_triplets: set[Tuple[str, str, str]]):
    def cat_name(ch):
        return ch.category.name if getattr(ch, "category", None) else ""

    for ch in list(guild.text_channels):
        key = (_norm(ch.name), "text", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
                # CHANGE: throttle after delete
                await _throttle()
            except Exception as e:
                print(f"[Messiah] text delete failed {ch.name}: {e}")

    for ch in list(guild.voice_channels):
        key = (_norm(ch.name), "voice", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
                # CHANGE: throttle after delete
                await _throttle()
            except Exception as e:
                print(f"[Messiah] voice delete failed {ch.name}: {e}")

    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in forums:
        key = (_norm(ch.name), "forum", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
                # CHANGE: throttle after delete
                await _throttle()
            except Exception as e:
                print(f"[Messiah] forum delete failed {ch.name}: {e}")


# ---------- main cog ----------
class ServerBuilder(commands.Cog):
    """MessiahBot: build/update server from form JSON (roles/categories/channels + perms, options, community)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="build_server", description="Messiah: Build server from latest saved layout")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def build_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        prog = Progress(interaction)
        await prog.set("starting full build…")
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        await prog.set("fetching layout…")
        layout = _load_layout_for_guild(interaction.guild.id)
        if not layout:
            await interaction.followup.send("❌ No layout found for this guild. Save one from the dashboard.", ephemeral=True)
            return

        try:
            await asyncio.wait_for(self._apply_layout(interaction.guild, layout, update_only=False, progress=prog), timeout=300)
            await interaction.followup.send("✅ Build complete.", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Build timed out. Some changes may have applied.", ephemeral=True)
        except Exception as e:
            print(f"[Messiah] build_server error: {e}")
            await interaction.followup.send(f"❌ Build crashed: `{e}`", ephemeral=True)

    @app_commands.command(name="update_server", description="Messiah: Update server to match latest saved layout")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def update_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        prog = Progress(interaction)
        await prog.set("starting update…")
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        await prog.set("fetching layout…")
        layout = _load_layout_for_guild(interaction.guild.id)
        if not layout:
            await interaction.followup.send("❌ No layout found for this guild. Save one from the dashboard.", ephemeral=True)
            return

        try:
            await asyncio.wait_for(self._apply_layout(interaction.guild, layout, update_only=True, progress=prog), timeout=300)
            await interaction.followup.send("✅ Update complete.", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Update timed out. Some changes may have applied.", ephemeral=True)
        except Exception as e:
            print(f"[Messiah] update_server error: {e}")
            await interaction.followup.send(f"❌ Update crashed: `{e}`", ephemeral=True)

    @app_commands.command(name="snapshot_layout", description="Messiah: Save current server structure as a new layout version")
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def snapshot_layout(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        if not (_psyco_ok and DATABASE_URL):
            await interaction.followup.send("❌ Database not configured on worker.", ephemeral=True)
            return

        # NEW: Fetch snapshot directly from worker (same as dashboard "Load From Live")
        worker_url = os.getenv("WORKER_URL")
        if not worker_url:
            await interaction.followup.send("❌ WORKER_URL not configured.", ephemeral=True)
            return

        try:
            resp = requests.get(f"{worker_url}/api/live_layout/{interaction.guild.id}", timeout=20)
            resp.raise_for_status()
            layout = resp.json()
        except Exception as e:
            await interaction.followup.send(f"❌ Worker snapshot failed: `{e}`", ephemeral=True)
            return

        try:
            with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Remove existing active rows before inserting new active layout
                    cur.execute(
                        "DELETE FROM builder_layouts WHERE guild_id=%s AND type='active'",
                        (str(interaction.guild.id),),
                    )
                    cur.execute(
                        "SELECT COALESCE(MAX(version),0)+1 AS v FROM builder_layouts WHERE guild_id=%s",
                        (str(interaction.guild.id),),
                    )
                    ver = int((cur.fetchone() or {}).get("v", 1))
                    cur.execute(
                        "INSERT INTO builder_layouts (guild_id, version, type, payload) VALUES (%s,%s,%s,%s::jsonb)",
                        (str(interaction.guild.id), ver, "active", json.dumps(layout)),
                    )
            await interaction.followup.send(
                f"✅ Saved layout snapshot as version {ver}. Open the dashboard and click **Load Latest From DB** to edit.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Snapshot failed while writing to DB: `{e}`", ephemeral=True)

    # ---------- core applier ----------
    async def _apply_layout(
        self,
        guild: discord.Guild,
        layout: Dict[str, Any],
        update_only: bool,
        progress: Optional[Progress] = None
    ):
        logs: List[str] = []
        ren_spec = (layout.get("renames") or {})
        prune_spec = (layout.get("prune") or {})

        # Normalize categories + channels (support nested and legacy)
        desired_categories: List[Tuple[str, Dict[str, Dict[str, str]]]] = []
        channels_spec: List[Dict[str, Any]] = []

        cats_payload = layout.get("categories", []) or []
        if cats_payload and isinstance(cats_payload[0], dict):
            for c in cats_payload:
                cname = c.get("name", "")
                desired_categories.append((cname, c.get("overwrites") or {}))

                text_list = c.get("channels_text") or []
                voice_list = c.get("channels_voice") or []
                merged = (c.get("channels") or []) + text_list + voice_list

                for ch in merged:
                    channels_spec.append({
                        "name": ch.get("name"),
                        "type": (ch.get("type") or "text").lower(),
                        "category": cname,
                        "topic": ch.get("topic") or (ch.get("options") or {}).get("topic"),
                        "options": ch.get("options") or {},
                        "overwrites": ch.get("overwrites") or {}
                    })
        else:
            for c in cats_payload:
                desired_categories.append((c, {}))
            for ch in (layout.get("channels") or []):
                channels_spec.append(ch)

        # Renames first
        if progress: await progress.set("applying renames…")
        await _apply_role_renames(guild, (ren_spec.get("roles") or []))
        await _apply_category_renames(guild, (ren_spec.get("categories") or []))
        await _apply_channel_renames(guild, (ren_spec.get("channels") or []))

        # Roles
        if progress: await progress.set("ensuring roles…")
        for r in layout.get("roles", []):
            name = _norm(r.get("name"))
            if not name:
                continue
            color = _hex_to_color(r.get("color"))
            has_perms = ("perms" in r) and isinstance(r.get("perms"), dict)
            perms_obj = _role_perms_from_flags(r.get("perms") or {}) if has_perms else None

            existing = _find_role(guild, name)
            if existing is None:
                try:
                    kwargs = dict(name=name, colour=color, reason="MessiahBot builder")
                    if has_perms and perms_obj is not None:
                        kwargs["permissions"] = perms_obj
                    await guild.create_role(**kwargs)
                    # CHANGE: throttle after create
                    await _throttle()
                    logs.append(f"✅ Role created: **{name}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create role: **{name}**")
            else:
                try:
                    kwargs = dict(colour=color, reason="MessiahBot update role")
                    if has_perms and perms_obj is not None:
                        kwargs["permissions"] = perms_obj
                    # If has_perms is False, omit 'permissions' so we preserve existing role perms
                    await existing.edit(**kwargs)
                    # CHANGE: throttle after edit
                    await _throttle()
                    logs.append(f"🔄 Role updated: **{name}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to edit role: **{name}**")

        # Categories
        if progress: await progress.set("ensuring categories…")
        cat_cache: Dict[str, discord.CategoryChannel] = {}
        for cname, cat_ow in desired_categories:
            cname_n = _norm(cname)
            if not cname_n:
                continue
            cat = _find_category(guild, cname_n)
            if cat is None:
                try:
                    ow = _build_overwrites(guild, cat_ow)
                    cat = await guild.create_category(cname_n, overwrites=(ow if isinstance(ow, dict) else None), reason="MessiahBot builder")
                    # CHANGE: throttle after create
                    await _throttle()
                    logs.append(f"✅ Category created: **{cname_n}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create category: **{cname_n}**")
            else:
                if cat_ow:
                    try:
                        ow = _build_overwrites(guild, cat_ow)
                        await cat.edit(overwrites=(ow if isinstance(ow, dict) else None), reason="MessiahBot update category overwrites")
                        # CHANGE: throttle after edit
                        await _throttle()
                        logs.append(f"🔧 Category overwrites set: **{cname_n}**")
                    except Exception:
                        logs.append(f"⚠️ Could not edit overwrites: **{cname_n}**")
                else:
                    logs.append(f"⏭️ Category exists: **{cname_n}**")

            if cat:
                cat_cache[cname_n] = cat

        # Channels
        if progress: await progress.set("ensuring channels…")
        for ch in channels_spec:
            chname = _norm(ch.get("name"))
            chtype = (ch.get("type") or "text").lower()
            catname = _norm(ch.get("category"))
            if not chname:
                continue

            parent = None
            if catname:
                parent = _find_category(guild, catname) or cat_cache.get(catname)
                if parent is None:
                    try:
                        parent = await guild.create_category(catname, reason="MessiahBot builder (parent for channel)")
                        # CHANGE: throttle after create
                        await _throttle()
                        cat_cache[catname] = parent
                        logs.append(f"✅ Category created for parent: **{catname}**")
                    except discord.Forbidden:
                        logs.append(f"❌ Missing permission to create category: **{catname}**")

            existing = None
            if chtype in ("text", "announcement"):
                existing = _find_text(guild, chname)
            elif chtype == "voice":
                existing = _find_voice(guild, chname)
            elif chtype == "forum":
                existing = _find_forum(guild, chname)
            elif chtype == "stage":
                existing = _find_voice(guild, chname)
            else:
                existing = _find_text(guild, chname)
                chtype = "text"

            ch_overwrites = _build_overwrites(guild, ch.get("overwrites") or {})
            if not isinstance(ch_overwrites, dict) or len(ch_overwrites) == 0:
                ch_overwrites = None  # avoid "overwrites expects a dict"
            opts = ch.get("options") or {}
            topic = ch.get("topic") or opts.get("topic") or None
            nsfw = bool(opts.get("nsfw") or opts.get("age_restricted"))
            slowmode = int(opts.get("slowmode") or 0)
            is_announcement = (chtype == "announcement")

            if existing is None:
                try:
                    created = None
                    if chtype in ("text", "announcement"):
                        created = await guild.create_text_channel(
                            chname, category=parent, overwrites=ch_overwrites, reason="MessiahBot builder"
                        )
                        # CHANGE: throttle after create
                        await _throttle()
                        # Try convert to news if requested
                        try:
                            if is_announcement and hasattr(discord, "ChannelType") and created.type != discord.ChannelType.news:
                                await created.edit(type=discord.ChannelType.news)
                                # CHANGE: throttle after edit
                                await _throttle()
                        except Exception:
                            pass
                    elif chtype == "voice":
                        created = await guild.create_voice_channel(
                            chname, category=parent, overwrites=ch_overwrites, reason="MessiahBot builder"
                        )
                        # CHANGE: throttle after create
                        await _throttle()
                    elif chtype == "forum":
                        if hasattr(guild, "create_forum"):
                            created = await guild.create_forum(name=chname, category=parent, reason="MessiahBot builder")
                            # CHANGE: throttle after create
                            await _throttle()
                        elif hasattr(guild, "create_forum_channel"):
                            created = await guild.create_forum_channel(name=chname, category=parent, reason="MessiahBot builder")
                            # CHANGE: throttle after create
                            await _throttle()
                    elif chtype == "stage":
                        if hasattr(guild, "create_stage_channel"):
                            created = await guild.create_stage_channel(chname, category=parent, reason="MessiahBot builder")
                            # CHANGE: throttle after create
                            await _throttle()

                    if created:
                        try:
                            kw = {}
                            if hasattr(created, "topic") and topic is not None: kw["topic"] = topic
                            if hasattr(created, "nsfw"): kw["nsfw"] = nsfw
                            if hasattr(created, "slowmode_delay"): kw["slowmode_delay"] = slowmode
                            if kw:
                                await created.edit(**kw)
                                # CHANGE: throttle after edit
                                await _throttle()
                        except Exception:
                            pass

                    logs.append(f"✅ Channel created: **#{chname}** [{chtype}]{' → ' + parent.name if parent else ''}")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname}**")
            else:
                try:
                    need_parent_id = parent.id if parent else None
                    has_parent_id = existing.category.id if getattr(existing, "category", None) else None
                    if need_parent_id != has_parent_id:
                        await existing.edit(category=parent, reason="MessiahBot move to correct category")
                        # CHANGE: throttle after edit
                        await _throttle()
                        logs.append(f"🔀 Moved **#{chname}** → **{parent.name if parent else 'no category'}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to move channel: **{chname}**")

                if ch_overwrites:
                    try:
                        await existing.edit(overwrites=ch_overwrites, reason="MessiahBot update overwrites")
                        # CHANGE: throttle after edit
                        await _throttle()
                        logs.append(f"🔧 Overwrites set: **#{chname}**")
                    except Exception:
                        logs.append(f"⚠️ Could not edit overwrites: **#{chname}**")

                try:
                    kw = {}
                    if hasattr(existing, "topic") and topic is not None: kw["topic"] = topic
                    if hasattr(existing, "nsfw"): kw["nsfw"] = nsfw
                    if hasattr(existing, "slowmode_delay"): kw["slowmode_delay"] = slowmode
                    if kw:
                        await existing.edit(**kw)
                        # CHANGE: throttle after edit
                        await _throttle()
                except Exception:
                    pass

        # Ordering (roles, categories, channels)
        if progress: await progress.set("ordering roles/categories/channels…")

        # --- Roles order ---
        try:
            desired_roles = [( _norm(r.get("name")), r.get("position") ) for r in (layout.get("roles") or []) if r.get("name")]
            # If explicit positions exist, sort by them; otherwise preserve given sequence
            if any(isinstance(p, int) for _, p in desired_roles):
                desired_roles.sort(key=lambda t: (999999 if t[1] is None else int(t[1])))
            else:
                # keep incoming order
                desired_roles = [(name, i) for i, (name, _) in enumerate(desired_roles)]
            # Build positions map for discord.py: higher number -> higher role
            positions_map: Dict[discord.Role, int] = {}
            top_base = max((getattr(r, "position", 1) for r in guild.roles), default=1) + len(desired_roles) + 5
            for i, (name, _) in enumerate(desired_roles):
                role_obj = _find_role(guild, name)
                if role_obj and not role_obj.is_default() and not role_obj.managed:
                    # assign descending targets so first in list ends up highest
                    positions_map[role_obj] = top_base - i
            if positions_map:
                try:
                    await guild.edit_role_positions(positions=positions_map)
                    # CHANGE: throttle after bulk reorder
                    await _throttle()
                    logs.append("📐 Roles reordered.")
                except AttributeError:
                    # Older discord.py fallback: try editing individual positions
                    for i, (name, _) in enumerate(reversed([x for x in desired_roles if _find_role(guild, x[0])])):
                        role_obj = _find_role(guild, name)
                        if role_obj:
                            try:
                                await role_obj.edit(position=(top_base - i))
                                # CHANGE: throttle after edit
                                await _throttle()
                            except Exception:
                                pass
                    logs.append("📐 Roles reordered (fallback).")
        except Exception as e:
            logs.append(f"⚠️ Could not reorder roles: {e}")

        # --- Categories order ---
        try:
            desired_cats = layout.get("categories") or []
            if desired_cats and isinstance(desired_cats[0], dict):
                tmp = []
                for idx, c in enumerate(desired_cats):
                    nm = _norm(c.get("name"))
                    pos = c.get("position")
                    tmp.append((nm, idx if pos is None else int(pos)))
                # sort categories by their intended positions
                tmp.sort(key=lambda x: x[1])
                for nm, pos in tmp:
                    cat = _find_category(guild, nm)
                    if cat:
                        try:
                            await cat.edit(position=pos, reason="MessiahBot reorder categories")
                            await _throttle()
                        except Exception:
                            pass
                if tmp:
                    logs.append("📐 Categories reordered.")
            else:
                # Legacy flat list, reorder by index
                for idx, nm in enumerate([_norm(x) for x in desired_cats if _norm(x)]):
                    cat = _find_category(guild, nm)
                    if cat:
                        try:
                            await cat.edit(position=idx, reason="MessiahBot reorder categories")
                            await _throttle()
                        except Exception:
                            pass
                if desired_cats:
                    logs.append("📐 Categories reordered (legacy).")
        except Exception as e:
            logs.append(f"⚠️ Could not reorder categories: {e}")

        # --- Channels order within each category (and uncategorized) ---
        try:
            # Build mapping from category name -> desired channel name order list (with types)
            if desired_cats and isinstance(desired_cats[0], dict):
                for c_idx, c in enumerate(desired_cats):
                    cname = _norm(c.get("name"))
                    desired_chs = c.get("channels") or []
                    # order by given position if present
                    desired_chs_sorted = list(desired_chs)
                    
                    parent = _find_category(guild, cname) if cname else None
                    for ch_idx, ch in enumerate(desired_chs_sorted):
                        nm = _norm(ch.get("name"))
                        typ = (ch.get("type") or "text").lower()
                        if not nm:
                            continue
                        # Find the existing channel of the right type
                        target = None
                        if typ in ("text", "announcement"):
                            target = _find_text(guild, nm)
                        elif typ in ("voice", "stage"):
                            target = _find_voice(guild, nm)
                        elif typ == "forum":
                            target = _find_forum(guild, nm)
                        if not target:
                            continue
                        try:
                            # ensure parent is correct (already done earlier, but safe)
                            if (parent and getattr(target, "category", None) != parent) or (not parent and getattr(target, "category", None) is not None):
                                await target.edit(category=parent, reason="MessiahBot move for ordering")
                                # CHANGE: throttle after edit
                                await _throttle()
                            desired_pos = ch.get("position")
                            await target.edit(
                                position=desired_pos if desired_pos is not None else ch_idx,
                                reason="MessiahBot reorder channels"
                            )
                            # CHANGE: throttle after edit
                            await _throttle()
                        except Exception:
                            pass
                logs.append("📐 Channels reordered within categories.")
            else:
                # Legacy flat layout: we can't reliably know per-category order beyond creation; skip.
                pass
        except Exception as e:
            logs.append(f"⚠️ Could not reorder channels: {e}")

        # Community
        if progress: await progress.set("applying community settings…")
        await _apply_community(guild, layout.get("community") or {}, is_build=(not update_only))

        # Prune
        if progress: await progress.set("pruning extras…")
        if prune_spec.get("roles"):
            wanted_roles = { _norm(r.get("name","")) for r in (layout.get("roles") or []) if r.get("name") }
            await _prune_roles(guild, wanted_roles)

        if prune_spec.get("categories"):
            wanted_cats = set()
            for c in layout.get("categories", []):
                if isinstance(c, str):
                    if c: wanted_cats.add(_norm(c))
                elif isinstance(c, dict):
                    nm = _norm(c.get("name"))
                    if nm: wanted_cats.add(nm)
            await _prune_categories(guild, wanted_cats)

        if prune_spec.get("channels"):
            wanted_chans: set[Tuple[str,str,str]] = set()
            for ch in channels_spec:
                nm = _norm(ch.get("name",""))
                tp = (ch.get("type") or "text").lower()
                cat = _norm(ch.get("category",""))
                if nm:
                    wanted_chans.add((nm, tp, cat))
            await _prune_channels(guild, wanted_chans)

        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))
        if progress: await progress.set("done.")


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))