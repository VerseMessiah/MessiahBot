# bot/commands_messiah_dc/server_builder.py
from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional, Tuple
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


# ---------- DB / layout helpers ----------

def _load_layout_for_guild(guild_id: int):
    """Load the latest saved layout for this guild from DB, or local file as fallback."""
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

    # Local fallback for dev
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


# ---------- permissions / overwrites ----------

def _role_perms_from_flags(flags: Dict[str, bool]) -> discord.Permissions:
    """Map simple role permission flags from the form to a Permissions object."""
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
        "send": "...",
        "connect":"...",
        "speak":"...",
        "manage_channels":"...",
        "manage_roles":"..."
      },
      ...
    }
    """
    out: Dict[discord.Role, discord.PermissionOverwrite] = {}
    if not ow_spec:
        return out

    def setp(ow: discord.PermissionOverwrite, attr: str, val: str):
        if val == "allow": setattr(ow, attr, True)
        elif val == "deny": setattr(ow, attr, False)
        else: setattr(ow, attr, None)

    for role_name, perms in ow_spec.items():
        role = _find_role(guild, role_name)
        if not role:
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


# ---------- snapshot (used by /snapshot_layout) ----------

def _snapshot_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Build a layout dict from the live guild (roles, categories, channels)."""
    # Roles (skip @everyone + managed)
    roles = []
    for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if r.is_default() or r.managed:
            continue
        roles.append({"name": r.name, "color": f"#{r.colour.value:06x}"})

    # Categories (ordered)
    def safe_pos(c):  # some objects can have None or raise
        try:
            return getattr(c, "position", 0) or 0
        except Exception:
            return 0
    categories = [c.name for c in sorted(guild.categories, key=safe_pos)]

    # Channels (ordered by parent category, then channel position)
    channels: List[Dict[str, Any]] = []

    def parent_key(ch):
        cat = getattr(ch, "category", None)
        return (safe_pos(cat) if cat else -1, safe_pos(ch))

    # text + news(announcement) live in text_channels
    for ch in sorted(guild.text_channels, key=parent_key):
        chtype = "announcement" if str(getattr(ch, "type", "")).endswith("news") else "text"
        channels.append({
            "name": ch.name,
            "type": chtype,
            "category": ch.category.name if ch.category else "",
            "options": {
                "topic": getattr(ch, "topic", None) or "",
                "nsfw": bool(getattr(ch, "nsfw", False)),
                "slowmode": int(getattr(ch, "slowmode_delay", 0) or 0),
            }
        })

    # voice
    for ch in sorted(guild.voice_channels, key=parent_key):
        channels.append({
            "name": ch.name,
            "type": "voice",
            "category": ch.category.name if ch.category else "",
            "options": {}
        })

    # forums (if enumerable)
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in sorted(forums, key=parent_key):
        channels.append({
            "name": ch.name,
            "type": "forum",
            "category": ch.category.name if ch.category else "",
            "options": {}
        })

    print(f"[Messiah snapshot] roles={len(roles)} cats={len(categories)} chans={len(channels)} in '{guild.name}'")

    return {
        "mode": "update",
        "roles": roles,
        "categories": categories,
        "channels": channels,
        "prune": {"roles": False, "categories": False, "channels": False},
        "renames": {"roles": [], "categories": [], "channels": []},
        "community": {"enable_on_build": False, "settings": {}}
    }
# --------------------------------------------------------------------------

# ---------- community settings ----------

async def _apply_community(guild: discord.Guild, community_payload: Dict[str, Any], is_build: bool):
    if not community_payload:
        return
    enable_on_build = bool(community_payload.get("enable_on_build"))
    settings = community_payload.get("settings") or {}

    # Enable community during build if requested
    if is_build and enable_on_build:
        try:
            await guild.edit(community=True)
        except Exception as e:
            print(f"[Messiah] community enable failed: {e}")

    # Only apply further settings if guild is community-capable
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
            return await guild.create_text_channel(nm, reason="MessiahBot community channel")
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
            except Exception as e:
                print(f"[Messiah] category rename failed {cat.name} -> {dst}: {e}")

async def _apply_channel_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    # text + voice + forum
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
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _prune_categories(guild: discord.Guild, desired_names: set[str]):
    for c in guild.categories:
        if _norm(c.name) not in desired_names:
            # only delete if empty (safer)
            if len(c.channels) == 0:
                try:
                    await c.delete(reason="Messiah prune (not in layout)")
                except Exception as e:
                    print(f"[Messiah] category delete failed {c.name}: {e}")

async def _prune_channels(guild: discord.Guild, desired_triplets: set[Tuple[str, str, str]]):
    def cat_name(ch):
        return ch.category.name if getattr(ch, "category", None) else ""

    # text
    for ch in list(guild.text_channels):
        key = (_norm(ch.name), "text", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] text delete failed {ch.name}: {e}")

    # voice
    for ch in list(guild.voice_channels):
        key = (_norm(ch.name), "voice", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] voice delete failed {ch.name}: {e}")

    # forums
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in forums:
        key = (_norm(ch.name), "forum", _norm(cat_name(ch)))
        if key not in desired_triplets:
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] forum delete failed {ch.name}: {e}")


# ---------- main cog ----------

class ServerBuilder(commands.Cog):
    """MessiahBot: build/update server from form JSON (roles/categories/channels + perms, options, community)."""

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

        # Apply full build
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

    @app_commands.command(name="snapshot_layout", description="Messiah: Save current server structure as a new layout version")
    @app_commands.checks.has_permissions(administrator=True)
    async def snapshot_layout(self, interaction: discord.Interaction):
        if not (_psyco_ok and DATABASE_URL):
            await interaction.response.send_message("❌ Database not configured on worker.", ephemeral=True)
            return

        await interaction.response.send_message("📸 Snapshotting current server…", ephemeral=True)
        layout = _snapshot_guild(interaction.guild)

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
            f"✅ Saved layout snapshot as version {ver}. Open the dashboard and click **Load Latest From DB** to edit.",
            ephemeral=True
        )

    # ---------- core applier ----------

    async def _apply_layout(self, guild: discord.Guild, layout: Dict[str, Any], update_only: bool):
        """
        layout = {
          "mode": "build"|"update",
          "roles": [{"name": str, "color": "#RRGGBB", "perms": {...}}],

          # Categories can be either a simple string list OR objects with overwrites and nested channels.
          # Preferred (nested) shape coming from the dashboard:
          # "categories": [
          #   {"name": "Category A", "overwrites": {...}, "channels": [
          #       {"name":"chat","type":"text|voice|forum|announcement|stage","topic":"...", "overwrites": {...}},
          #       {"name":"voice-1","type":"voice"}
          #   ]},
          #   {"name": "", "channels":[ ... ]}  # empty name = uncategorized bucket
          # ]
          #
          # Legacy/flat shape (still supported):
          # "categories": [ "Cat A", "Cat B" ],
          # "channels":   [{"name": "...", "type": "text|voice|forum|announcement|stage", "category": "Cat A",
          #                 "options": {...}, "topic":"...", "overwrites": {...}}]

          "prune": {"roles": bool, "categories": bool, "channels": bool},
          "renames": {"roles":[{from,to}], "categories":[{from,to}], "channels":[{from,to,category}]},
          "community": {...}
        }
        """
        logs: List[str] = []
        ren_spec = (layout.get("renames") or {})
        prune_spec = (layout.get("prune") or {})

        # ---------- Normalize categories + channels from payload ----------
        # We support both nested (preferred) and legacy flat shapes.
        desired_categories: List[Tuple[str, Dict[str, Dict[str, str]]]] = []
        channels_spec: List[Dict[str, Any]] = []

        cats_payload = layout.get("categories", []) or []
        if cats_payload and isinstance(cats_payload[0], dict):
            # Nested shape
            for c in cats_payload:
                cname = c.get("name", "")
                desired_categories.append((cname, c.get("overwrites") or {}))
                for ch in (c.get("channels") or []):
                    channels_spec.append({
                        "name": ch.get("name"),
                        "type": (ch.get("type") or "text").lower(),
                        "category": cname,
                        "topic": ch.get("topic") or (ch.get("options") or {}).get("topic"),
                        "options": ch.get("options") or {},
                        "overwrites": ch.get("overwrites") or {}
                    })
        else:
            # Legacy shape
            for c in cats_payload:
                desired_categories.append((c, {}))
            for ch in (layout.get("channels") or []):
                channels_spec.append(ch)

        # ---------- Apply renames first (prevents duplicate create when only renaming) ----------
        # Roles
        await _apply_role_renames(guild, (ren_spec.get("roles") or []))
        # Categories
        await _apply_category_renames(guild, (ren_spec.get("categories") or []))
        # Channels
        await _apply_channel_renames(guild, (ren_spec.get("channels") or []))

        # ---------- ROLES (create/edit + perms/color) ----------
        for r in layout.get("roles", []):
            name = _norm(r.get("name"))
            if not name:
                continue
            color = _hex_to_color(r.get("color"))
            perm_flags = r.get("perms") or {}
            perms_obj = _role_perms_from_flags(perm_flags)

            existing = _find_role(guild, name)
            if existing is None:
                try:
                    await guild.create_role(name=name, colour=color, permissions=perms_obj, reason="MessiahBot builder")
                    logs.append(f"✅ Role created: **{name}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create role: **{name}**")
            else:
                try:
                    await existing.edit(colour=color, permissions=perms_obj, reason="MessiahBot update role")
                    logs.append(f"🔄 Role updated: **{name}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to edit role: **{name}**")

        # ---------- CATEGORIES (create + overwrites) ----------
        cat_cache: Dict[str, discord.CategoryChannel] = {}
        for cname, cat_ow in desired_categories:
            cname_n = _norm(cname)
            if not cname_n:
                continue
            cat = _find_category(guild, cname_n)
            if cat is None:
                try:
                    ow = _build_overwrites(guild, cat_ow)
                    cat = await guild.create_category(cname_n, overwrites=(ow or None), reason="MessiahBot builder")
                    logs.append(f"✅ Category created: **{cname_n}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create category: **{cname_n}**")
            else:
                if cat_ow:
                    try:
                        await cat.edit(overwrites=_build_overwrites(guild, cat_ow), reason="MessiahBot update category overwrites")
                        logs.append(f"🔧 Category overwrites set: **{cname_n}**")
                    except Exception:
                        logs.append(f"⚠️ Could not edit overwrites: **{cname_n}**")
                else:
                    logs.append(f"⏭️ Category exists: **{cname_n}**")

            if cat:
                cat_cache[cname_n] = cat

        # ---------- CHANNELS: create/move/place FIRST ----------
        for ch in channels_spec:
            chname = _norm(ch.get("name"))
            chtype = (ch.get("type") or "text").lower()
            catname = _norm(ch.get("category"))
            if not chname:
                continue

            # Parent category
            parent = None
            if catname:
                parent = _find_category(guild, catname) or cat_cache.get(catname)
                if parent is None:
                    # make parent on the fly
                    try:
                        parent = await guild.create_category(catname, reason="MessiahBot builder (parent for channel)")
                        cat_cache[catname] = parent
                        logs.append(f"✅ Category created for parent: **{catname}**")
                    except discord.Forbidden:
                        logs.append(f"❌ Missing permission to create category: **{catname}**")

            # find existing (by type)
            existing = None
            if chtype == "text" or chtype == "announcement":
                existing = _find_text(guild, chname)
            elif chtype == "voice":
                existing = _find_voice(guild, chname)
            elif chtype == "forum":
                existing = _find_forum(guild, chname)
            elif chtype == "stage":
                # Stage channels are part of voice category in discord.py; look by name in voice channels as fallback.
                existing = _find_voice(guild, chname)
            else:
                existing = _find_text(guild, chname)
                chtype = "text"

            # build overwrites + options
            ch_overwrites = _build_overwrites(guild, ch.get("overwrites") or {})
            opts = ch.get("options") or {}
            topic = ch.get("topic") or opts.get("topic") or None
            nsfw = bool(opts.get("nsfw") or opts.get("age_restricted"))
            slowmode = int(opts.get("slowmode") or 0)
            is_announcement = (chtype == "announcement")

            if existing is None:
                try:
                    if chtype == "text" or is_announcement:
                        created = await guild.create_text_channel(
                            chname, category=parent, overwrites=(ch_overwrites or None), reason="MessiahBot builder"
                        )
                        # Try convert to news channel if requested
                        try:
                            if is_announcement and hasattr(discord, "ChannelType") and created.type != discord.ChannelType.news:
                                await created.edit(type=discord.ChannelType.news)
                        except Exception:
                            pass
                    elif chtype == "voice":
                        created = await guild.create_voice_channel(
                            chname, category=parent, overwrites=(ch_overwrites or None), reason="MessiahBot builder"
                        )
                    elif chtype == "forum":
                        created = await guild.create_forum(
                            name=chname, category=parent, reason="MessiahBot builder"
                        )
                    elif chtype == "stage":
                        # Stage channels require a Stage instance to be started by a user later; just create the channel.
                        created = await guild.create_stage_channel(
                            chname, category=parent, reason="MessiahBot builder"
                        )
                    else:
                        created = None

                    # post-create options
                    if created:
                        try:
                            kw = {}
                            if hasattr(created, "topic") and topic is not None: kw["topic"] = topic
                            if hasattr(created, "nsfw"): kw["nsfw"] = nsfw
                            if hasattr(created, "slowmode_delay"): kw["slowmode_delay"] = slowmode
                            if kw:
                                await created.edit(**kw)
                        except Exception:
                            pass

                    logs.append(f"✅ Channel created: **#{chname}** [{chtype}]{' → ' + parent.name if parent else ''}")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname}**")
            else:
                # move / set parent if needed
                try:
                    need_parent_id = parent.id if parent else None
                    has_parent_id = existing.category.id if getattr(existing, "category", None) else None
                    if need_parent_id != has_parent_id:
                        await existing.edit(category=parent, reason="MessiahBot move to correct category")
                        logs.append(f"🔀 Moved **#{chname}** → **{parent.name if parent else 'no category'}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to move channel: **{chname}**")

                # apply overwrites & options
                if ch_overwrites:
                    try:
                        await existing.edit(overwrites=ch_overwrites, reason="MessiahBot update overwrites")
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
                except Exception:
                    pass

        # (Renames now applied at the start, before create/move)

        # ---------- Community ----------
        await _apply_community(guild, layout.get("community") or {}, is_build=(not update_only))

        # ---------- PRUNE (based on final desired sets) ----------
        # Roles wanted
        if prune_spec.get("roles"):
            wanted_roles = { _norm(r.get("name","")) for r in (layout.get("roles") or []) if r.get("name") }
            await _prune_roles(guild, wanted_roles)

        # Categories wanted
        if prune_spec.get("categories"):
            wanted_cats = set()
            for c in layout.get("categories", []):
                if isinstance(c, str):
                    if c: wanted_cats.add(_norm(c))
                elif isinstance(c, dict):
                    nm = _norm(c.get("name"))
                    if nm: wanted_cats.add(nm)
            await _prune_categories(guild, wanted_cats)

        # Channels wanted
        if prune_spec.get("channels"):
            wanted_chans: set[Tuple[str,str,str]] = set()
            for ch in channels_spec:
                nm = _norm(ch.get("name",""))
                tp = (ch.get("type") or "text").lower()
                cat = _norm(ch.get("category",""))
                if nm:
                    wanted_chans.add((nm, tp, cat))
            await _prune_channels(guild, wanted_chans)

        # ---------- logging ----------
        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))