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


# ----------------------------- Helpers --------------------------------------

def _norm(name: Optional[str]) -> str:
    return (name or "").strip().lower()

def _hex_to_color(hex_str: Optional[str]) -> discord.Color:
    s = (hex_str or "").strip().lstrip("#")
    try:
        return discord.Color(int(s, 16))
    except Exception:
        return discord.Color.default()

def _find_role(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    nl = _norm(name)
    return next((r for r in guild.roles if _norm(r.name) == nl), None)

def _find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    nl = _norm(name)
    return next((c for c in guild.categories if _norm(c.name) == nl), None)

def _find_text(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    nl = _norm(name)
    return next((c for c in guild.text_channels if _norm(c.name) == nl), None)

def _find_voice(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    nl = _norm(name)
    return next((c for c in guild.voice_channels if _norm(c.name) == nl), None)

def _find_forum(guild: discord.Guild, name: str) -> Optional[discord.ForumChannel]:
    nl = _norm(name)
    try:
        return next((c for c in guild.forums if _norm(c.name) == nl), None)
    except Exception:
        return None

def _load_layout_for_guild(guild_id: int) -> Optional[Dict[str, Any]]:
    """Load latest saved layout for the guild from DB, with local-file fallback."""
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

    # Fallback for local testing
    path = os.getenv("LOCAL_LATEST_CONFIG", "latest_config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


# ------------------ Permission overwrites helpers ---------------------------

def _perm_overwrite_from_dict(guild: discord.Guild, d: Dict[str, Any]) -> Tuple[Optional[discord.abc.Snowflake], Optional[discord.PermissionOverwrite]]:
    """
    Accepts a dict like:
      { "type":"role"|"member", "name":"Mods" or "id":"123", "allow": int, "deny": int }
    Returns (target, PermissionOverwrite) or (None, None) if target not found.
    """
    allow = int(d.get("allow", 0) or 0)
    deny  = int(d.get("deny", 0) or 0)

    target = None
    dtype = (d.get("type") or "").lower()
    if dtype == "role":
        if d.get("id"):
            target = guild.get_role(int(d["id"]))
        elif d.get("name"):
            target = _find_role(guild, d["name"])
    elif dtype == "member":
        if d.get("id"):
            target = guild.get_member(int(d["id"]))
        # by-name member lookup is unreliable; prefer id

    if not target:
        return (None, None)

    # Build overwrite from bitfields
    # (discord.Permissions is iterable: yields (perm_name, bool))
    allow_p = discord.Permissions(allow)
    deny_p  = discord.Permissions(deny)

    ow_kwargs: Dict[str, Optional[bool]] = {}
    for name, val in allow_p:
        if val:
            ow_kwargs[name] = True
    for name, val in deny_p:
        if val:
            ow_kwargs[name] = False

    return (target, discord.PermissionOverwrite(**ow_kwargs))

async def _apply_overwrites(obj: discord.abc.GuildChannel, guild: discord.Guild, overwrites: Optional[List[Dict[str, Any]]]):
    if not overwrites:
        return
    mapping: Dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {}
    for d in overwrites or []:
        target, ow = _perm_overwrite_from_dict(guild, d)
        if target and ow:
            mapping[target] = ow
    if mapping:
        try:
            await obj.edit(overwrites=mapping, reason="Messiah apply overwrites")
        except Exception as e:
            print(f"[Messiah] overwrite edit failed for {getattr(obj,'name','?')}: {e}")


# ------------------------- Rename helpers -----------------------------------

async def _apply_role_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    by_name = { _norm(r.name): r for r in guild.roles }
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst: continue
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
        if not src or not dst: continue
        cat = by_name.get(src)
        if cat:
            try:
                await cat.edit(name=dst, reason="Messiah rename (layout)")
            except Exception as e:
                print(f"[Messiah] category rename failed {cat.name} -> {dst}: {e}")

async def _apply_channel_renames(guild: discord.Guild, renames: List[Dict[str, str]]):
    all_chans = list(guild.text_channels) + list(guild.voice_channels)
    try:
        all_chans += list(guild.forums)
    except Exception:
        pass
    by_name = { _norm(c.name): c for c in all_chans }
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst: continue
        ch = by_name.get(src)
        if ch:
            try:
                await ch.edit(name=dst, reason="Messiah rename (layout)")
            except Exception as e:
                print(f"[Messiah] channel rename failed {ch.name} -> {dst}: {e}")


# ---------------------------- Prune helpers ---------------------------------

async def _prune_roles(guild: discord.Guild, desired_names: set[str]):
    for r in guild.roles:
        if r.is_default() or r.managed:  # skip @everyone and managed roles
            continue
        if _norm(r.name) not in desired_names:
            try:
                await r.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _prune_categories(guild: discord.Guild, desired_names: set[str]):
    for c in guild.categories:
        if _norm(c.name) not in desired_names:
            if len(c.channels) == 0:  # only delete if empty (safer)
                try:
                    await c.delete(reason="Messiah prune (not in layout)")
                except Exception as e:
                    print(f"[Messiah] category delete failed {c.name}: {e}")

async def _prune_channels(guild: discord.Guild, desired_triplets: set[tuple]):
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


# ------------------------ Snapshot live layout ------------------------------

def _snapshot_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Build a layout dict from the live guild, including ordering and topics."""
    # Roles: skip @everyone and managed
    roles = []
    for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if r.is_default() or r.managed:
            continue
        roles.append({
            "name": r.name,
            "color": f"#{r.colour.value:06x}",
            "permissions": r.permissions.value
        })

    # Categories
    categories = [{
        "name": c.name,
        "position": c.position,
        # Overwrites are not directly readable as bitfields; leaving empty in snapshot
        # The dashboard live REST route can populate raw overwrites from Discord HTTP.
        "overwrites": []
    } for c in sorted(guild.categories, key=lambda x: x.position)]

    # Channels
    channels: List[Dict[str, Any]] = []
    # text
    for ch in sorted(guild.text_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({
            "name": ch.name, "type": "text",
            "category": ch.category.name if ch.category else "",
            "position": ch.position,
            "topic": ch.topic or "",
            "overwrites": []
        })
    # voice
    for ch in sorted(guild.voice_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({
            "name": ch.name, "type": "voice",
            "category": ch.category.name if ch.category else "",
            "position": ch.position,
            "topic": "",
            "overwrites": []
        })
    # forums
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in sorted(forums, key=lambda x: (x.category.position if x.category else -1, getattr(x, "position", 0))):
        channels.append({
            "name": ch.name, "type": "forum",
            "category": ch.category.name if ch.category else "",
            "position": getattr(ch, "position", 0),
            "topic": getattr(ch, "topic", "") or "",
            "overwrites": []
        })

    return {"mode": "update", "roles": roles, "categories": categories, "channels": channels}


# ------------------------------ Cog -----------------------------------------

class ServerBuilder(commands.Cog):
    """MessiahBot: build/update server from form JSON (roles/categories/channels/community)."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------- Slash commands ------------------------------

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

    # ----------------------------- Core apply -------------------------------

    async def _apply_layout(self, guild: discord.Guild, layout: Dict[str, Any], update_only: bool):
        """
        layout supports:
          roles[{name,color,#permissions}], categories[{name,position,overwrites[]}],
          channels[{name,type,category,position,topic,overwrites[]}],
          community{...}, renames{...}, prune{...}
        """
        logs: List[str] = []

        # --------------------- ROLES (create/edit + perms) -------------------
        for r in layout.get("roles", []) or []:
            name_n = _norm(r.get("name"))
            if not name_n:
                continue
            color = _hex_to_color(r.get("color"))
            perms_int = r.get("permissions")
            existing = _find_role(guild, name_n)
            if existing is None:
                try:
                    await guild.create_role(
                        name=name_n,
                        color=color,
                        permissions=discord.Permissions(perms_int) if perms_int is not None else discord.Permissions.none(),
                        reason="MessiahBot builder"
                    )
                    logs.append(f"✅ Role created: **{name_n}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create role: **{name_n}**")
            else:
                try:
                    if perms_int is not None:
                        await existing.edit(colour=color, permissions=discord.Permissions(perms_int), reason="MessiahBot update role")
                        logs.append(f"🔄 Role updated: **{name_n}**")
                    else:
                        await existing.edit(colour=color, reason="MessiahBot update role color")
                        logs.append(f"🔄 Role color updated: **{name_n}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to edit role: **{name_n}**")

        # Build a cache of categories as we go
        cat_cache: Dict[str, discord.CategoryChannel] = {}

        # ----------------- (1) ENSURE TARGET CATEGORIES EXIST ----------------
        for c in layout.get("categories", []) or []:
            cname = _norm(c["name"] if isinstance(c, dict) else c)
            if not cname:
                continue
            cat = _find_category(guild, cname)
            if cat is None:
                try:
                    cat = await guild.create_category(cname, reason="MessiahBot builder")
                    logs.append(f"✅ Category created: **{cname}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create category: **{cname}**")
            else:
                logs.append(f"⏭️ Category exists: **{cname}**")
            if cat:
                cat_cache[cname] = cat

        # -------------- (2) CREATE / MOVE CHANNELS TO TARGET CATS ------------
        for ch in layout.get("channels", []) or []:
            chname = _norm(ch.get("name"))
            if not chname:
                continue
            chtype = (ch.get("type") or "text").lower()
            catname = _norm(ch.get("category"))
            topic = ch.get("topic")
            overwrites = ch.get("overwrites")

            parent = None
            if catname:
                parent = _find_category(guild, catname) or cat_cache.get(catname)
                if parent is None:
                    try:
                        parent = await guild.create_category(catname, reason="MessiahBot builder (parent for channel)")
                        logs.append(f"✅ Category created for parent: **{catname}**")
                        cat_cache[catname] = parent
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
                    created = None
                    if chtype == "text":
                        created = await guild.create_text_channel(chname, category=parent, reason="MessiahBot builder")
                    elif chtype == "voice":
                        created = await guild.create_voice_channel(chname, category=parent, reason="MessiahBot builder")
                    elif chtype == "forum":
                        created = await guild.create_forum(name=chname, category=parent, reason="MessiahBot builder")
                    logs.append(f"✅ Channel created: **#{chname}** [{chtype}]{' → ' + parent.name if parent else ''}")

                    if created:
                        # apply topic / overwrites
                        if topic and hasattr(created, "edit"):
                            try:
                                await created.edit(topic=topic)
                            except Exception:
                                pass
                        await _apply_overwrites(created, guild, overwrites)
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname}**")
            else:
                try:
                    need_parent_id = parent.id if parent else None
                    has_parent_id = existing.category.id if getattr(existing, "category", None) else None
                    if need_parent_id != has_parent_id:
                        await existing.edit(category=parent, reason="MessiahBot move to correct category")
                        logs.append(f"🔀 Moved **#{chname}** → **{parent.name if parent else 'no category'}**")
                    # apply topic / overwrites
                    if topic is not None and hasattr(existing, "edit"):
                        try:
                            await existing.edit(topic=topic)
                        except Exception:
                            pass
                    await _apply_overwrites(existing, guild, overwrites)
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to move/edit channel: **{chname}**")

        # ------------------- (3) APPLY RENAMES *AFTER* MOVES -----------------
        ren = (layout.get("renames") or {})
        await _apply_role_renames(guild, ren.get("roles") or [])
        await _apply_category_renames(guild, ren.get("categories") or [])
        await _apply_channel_renames(guild, ren.get("channels") or [])

        # -------- (4) POSITIONS + CATEGORY OVERWRITES (best-effort) ---------
        # Categories: set position & overwrites
        for c in layout.get("categories", []) or []:
            if isinstance(c, dict):
                cname = _norm(c.get("name"))
                if not cname:
                    continue
                cat = _find_category(guild, cname)
                if not cat:
                    continue
                # Position
                if "position" in c and c.get("position") is not None:
                    try:
                        await cat.edit(position=int(c["position"]), reason="Messiah set category position")
                    except Exception as e:
                        print("cat position error:", e)
                # Overwrites
                await _apply_overwrites(cat, guild, c.get("overwrites"))

        # Channels: set position inside their categories (sorted to reduce churn)
        for ch in sorted(layout.get("channels", []) or [], key=lambda x: ( _norm(x.get("category")), int(x.get("position", 0) or 0) )):
            chname = _norm(ch.get("name"))
            chtype = (ch.get("type") or "text").lower()
            existing = None
            if chtype == "text":
                existing = _find_text(guild, chname)
            elif chtype == "voice":
                existing = _find_voice(guild, chname)
            elif chtype == "forum":
                existing = _find_forum(guild, chname)
            if existing and "position" in ch and ch.get("position") is not None:
                try:
                    await existing.edit(position=int(ch["position"]), reason="Messiah set channel position")
                except Exception as e:
                    print("channel position error:", e)

        # ------------------------- (5) COMMUNITY -----------------------------
        community = layout.get("community") or {}
        if community:
            try:
                kwargs: Dict[str, Any] = {}
                if community.get("description") is not None:
                    kwargs["description"] = community["description"]

                if community.get("rules_channel"):
                    rc = _find_text(guild, community["rules_channel"])
                    if rc: kwargs["rules_channel"] = rc
                if community.get("updates_channel"):
                    uc = _find_text(guild, community["updates_channel"])
                    if uc: kwargs["public_updates_channel"] = uc

                vl = community.get("verification_level")
                if vl:
                    kwargs["verification_level"] = getattr(discord.VerificationLevel, vl, discord.VerificationLevel.none)
                dn = community.get("default_notifications")
                if dn:
                    kwargs["default_notifications"] = getattr(discord.NotificationLevel, dn, discord.NotificationLevel.only_mentions)
                ecf = community.get("explicit_content_filter")
                if ecf:
                    kwargs["explicit_content_filter"] = getattr(discord.ContentFilter, ecf, discord.ContentFilter.disabled)

                if kwargs:
                    await guild.edit(reason="Messiah community settings", **kwargs)
            except discord.Forbidden:
                print("[Messiah] no permission to edit community settings")
            except Exception as e:
                print("[Messiah] community edit error:", e)

        # --------------------------- (6) PRUNE -------------------------------
        prune = (layout.get("prune") or {})
        if prune.get("roles"):
            wanted_roles = { _norm(r.get("name","")) for r in (layout.get("roles") or []) if r.get("name") }
            await _prune_roles(guild, wanted_roles)

        if prune.get("categories"):
            wanted_cats = { _norm(c.get("name") if isinstance(c, dict) else c) for c in (layout.get("categories") or []) if c }
            await _prune_categories(guild, wanted_cats)

        if prune.get("channels"):
            wanted_tris: set[tuple] = set()
            for ch in (layout.get("channels") or []):
                nm = _norm(ch.get("name",""))
                tp = (ch.get("type") or "text").lower()
                cat = _norm(ch.get("category",""))
                if nm:
                    wanted_tris.add((nm, tp, cat))
            await _prune_channels(guild, wanted_tris)

        # Optional: console summary
        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))
