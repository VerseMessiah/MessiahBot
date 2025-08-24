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


# -------------------------
# DB / layout loader
# -------------------------
def _load_layout_for_guild(guild_id: int):
    """Load latest saved layout for a guild from DB, else fallback to local JSON."""
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


# -------------------------
# Helpers
# -------------------------
def _norm(name: str) -> str:
    return (name or "").strip().lower()

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
    nl = name.lower()
    try:
        return next((c for c in guild.forums if c.name.lower() == nl), None)
    except Exception:
        return None

def _db_exec(q: str, params=()):
    if not (_psyco_ok and DATABASE_URL):
        raise RuntimeError("DATABASE_URL not configured or psycopg not available")
    with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(q, params)


# -------------------------
# Snapshot (used by /snapshot_layout)
# -------------------------
def _snapshot_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Build a layout dict from the live guild."""
    # Roles: skip @everyone and managed integration roles
    roles = []
    for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if r.is_default() or r.managed:
            continue
        roles.append({"name": r.name, "color": f"#{r.colour.value:06x}"})

    # Categories in display order
    categories = [c.name for c in sorted(guild.categories, key=lambda x: x.position)]

    # Channels with type + parent category
    channels = []
    # text
    for ch in sorted(guild.text_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({"name": ch.name, "type": "text", "category": ch.category.name if ch.category else ""})
    # voice
    for ch in sorted(guild.voice_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({"name": ch.name, "type": "voice", "category": ch.category.name if ch.category else ""})
    # forums (if available)
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in sorted(forums, key=lambda x: (x.category.position if x.category else -1, getattr(x, "position", 0))):
        channels.append({"name": ch.name, "type": "forum", "category": ch.category.name if ch.category else ""})

    return {"mode": "update", "roles": roles, "categories": categories, "channels": channels}


# -------------------------
# Rename helpers
# -------------------------
async def _apply_role_renames(guild: discord.Guild, renames: List[dict]):
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
                print(f"[Messiah] role rename failed {getattr(role,'name',src)} -> {dst}: {e}")

async def _apply_category_renames(guild: discord.Guild, renames: List[dict]):
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
                print(f"[Messiah] category rename failed {getattr(cat,'name',src)} -> {dst}: {e}")

async def _apply_channel_renames(guild: discord.Guild, renames: List[dict]):
    all_chans = list(guild.text_channels) + list(guild.voice_channels)
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
                print(f"[Messiah] channel rename failed {getattr(ch,'name',src)} -> {dst}: {e}")

async def _delete_roles(guild: discord.Guild, names: list[str]):
    wanted = { _norm(n) for n in (names or []) if n }
    for r in guild.roles:
        if r.is_default() or r.managed:
            continue
        if _norm(r.name) in wanted:
            try:
                await r.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _delete_channels(guild: discord.Guild, items: list[dict]):
    # items: [{name,type,category}]
    def cat_name(ch): return ch.category.name if getattr(ch, "category", None) else ""
    wanted = set()
    for it in (items or []):
        nm = _norm(it.get("name",""))
        tp = (it.get("type") or "text").lower()
        cat = _norm(it.get("category",""))
        if nm:
            wanted.add((nm,tp,cat))

    # text
    for ch in list(guild.text_channels):
        key = (_norm(ch.name), "text", _norm(cat_name(ch)))
        if key in wanted:
            try:
                await ch.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] text delete failed {ch.name}: {e}")
    # voice
    for ch in list(guild.voice_channels):
        key = (_norm(ch.name), "voice", _norm(cat_name(ch)))
        if key in wanted:
            try:
                await ch.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] voice delete failed {ch.name}: {e}")
    # forums
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in forums:
        key = (_norm(ch.name), "forum", _norm(cat_name(ch)))
        if key in wanted:
            try:
                await ch.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] forum delete failed {ch.name}: {e}")

async def _delete_categories_with_reassign(guild: discord.Guild, items: list[dict]):
    # items: [{"name": str, "reassign_to": str|""}]
    # move channels out, then delete the category
    # Build a name->category map for quick lookup
    by_name = { _norm(c.name): c for c in guild.categories }
    for it in (items or []):
        name = _norm(it.get("name",""))
        if not name: continue
        cat = by_name.get(name)
        if not cat: continue

        target_name = _norm(it.get("reassign_to",""))
        target_cat = by_name.get(target_name) if target_name else None

        # Move channels if any
        for ch in list(cat.channels):
            try:
                await ch.edit(category=target_cat, reason="Messiah reassign before category delete")
            except Exception as e:
                print(f"[Messiah] failed to reassign channel {getattr(ch,'name','?')} from {cat.name}: {e}")

        # Delete the now-empty category
        try:
            await cat.delete(reason="Messiah explicit delete (layout)")
        except Exception as e:
            print(f"[Messiah] category delete failed {cat.name}: {e}")


# -------------------------
# PRUNE helpers (module-level, so they can be called from commands)
# -------------------------
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

async def _prune_channels(guild: discord.Guild, desired_triplets: set[tuple]):
    # Compare by (name, type, parent-category-name)
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


# -------------------------
# COG
# -------------------------
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

        # Apply renames first (safe if not provided)
        ren = (layout or {}).get("renames", {})
        await _apply_role_renames(interaction.guild, ren.get("roles") or [])
        await _apply_category_renames(interaction.guild, ren.get("categories") or [])
        await _apply_channel_renames(interaction.guild, ren.get("channels") or [])

        # Apply layout (create/update/move)
        await self._apply_layout(interaction.guild, layout, update_only=False)

        # Explicit deletions (safe order): channels -> categories (with reassign) -> roles
        dels = (layout.get("deletions") or {})
        await _delete_channels(interaction.guild, dels.get("channels") or [])
        await _delete_categories_with_reassign(interaction.guild, dels.get("categories") or [])
        await _delete_roles(interaction.guild, dels.get("roles") or [])


        await interaction.followup.send("✅ Build complete.", ephemeral=True)

    @app_commands.command(name="update_server", description="Messiah: Update server to match latest saved layout")
    @app_commands.checks.has_permissions(administrator=True)
    async def update_server(self, interaction: discord.Interaction):
        await interaction.response.send_message("🔧 Messiah applying **update**…", ephemeral=True)

        layout = _load_layout_for_guild(interaction.guild.id)
        if not layout:
            await interaction.followup.send("❌ No layout found for this guild. Save one from the dashboard.", ephemeral=True)
            return

        # Apply renames first
        ren = (layout or {}).get("renames", {})
        await _apply_role_renames(interaction.guild, ren.get("roles") or [])
        await _apply_category_renames(interaction.guild, ren.get("categories") or [])
        await _apply_channel_renames(interaction.guild, ren.get("channels") or [])

        # Upsert (create/update/move)
        await self._apply_layout(interaction.guild, layout, update_only=True)

        # PRUNE (after upsert) if requested
        prune = (layout.get("prune") or {})
        if prune.get("roles"):
            wanted_roles = { _norm(r.get("name","")) for r in (layout.get("roles") or []) if r.get("name") }
            await _prune_roles(interaction.guild, wanted_roles)

        if prune.get("categories"):
            wanted_cats = { _norm(n) for n in (layout.get("categories") or []) if n }
            await _prune_categories(interaction.guild, wanted_cats)

        if prune.get("channels"):
            wanted_triplets: set[tuple] = set()
            for ch in (layout.get("channels") or []):
                nm = _norm(ch.get("name",""))
                tp = (ch.get("type") or "text").lower()
                cat = _norm(ch.get("category",""))
                if nm:
                    wanted_triplets.add((nm, tp, cat))
            await _prune_channels(interaction.guild, wanted_triplets)

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

    # -------------------------
    # Core upsert logic
    # -------------------------
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
            raw_name = r.get("name")
            name = _norm(raw_name)
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
                # NOTE: discord.py may require create_forum_channel for creation;
                # we still match existing by name if property available
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
                        # Some versions use create_forum_channel
                        create_forum = getattr(guild, "create_forum", None) or getattr(guild, "create_forum_channel", None)
                        if create_forum:
                            await create_forum(name=chname, category=parent, reason="MessiahBot builder")
                        else:
                            raise RuntimeError("This discord.py version lacks forum creation API")
                    logs.append(f"✅ Channel created: **#{chname}** [{chtype}]{' → ' + parent.name if parent else ''}")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname}**")
                except Exception as e:
                    logs.append(f"❌ Failed to create channel **{chname}**: {e}")
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

        # Optional: print summary in console
        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))
