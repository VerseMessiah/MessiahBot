from __future__ import annotations
import os, json
from typing import Dict, Any, List, Optional, Tuple, Set
import discord
from discord.ext import commands
from discord import app_commands

# --- Optional Postgres for snapshot command ---
DATABASE_URL = os.getenv("DATABASE_URL")
_psyco_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psyco_ok = True
except Exception:
    _psyco_ok = False


# =========================
# Common helpers
# =========================
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _hex_to_color(hex_str: Optional[str]) -> discord.Color:
    s = (hex_str or "").strip().lstrip("#")
    try:
        return discord.Color(int(s, 16))
    except Exception:
        return discord.Color.default()

def _find_role(guild: discord.Guild, name_norm: str) -> Optional[discord.Role]:
    nl = name_norm
    return next((r for r in guild.roles if r.name.lower() == nl), None)

def _find_category(guild: discord.Guild, name_norm: str) -> Optional[discord.CategoryChannel]:
    nl = name_norm
    return next((c for c in guild.categories if c.name.lower() == nl), None)

def _find_text(guild: discord.Guild, name_norm: str) -> Optional[discord.TextChannel]:
    nl = name_norm
    return next((c for c in guild.text_channels if c.name.lower() == nl), None)

def _find_voice(guild: discord.Guild, name_norm: str) -> Optional[discord.VoiceChannel]:
    nl = name_norm
    return next((c for c in guild.voice_channels if c.name.lower() == nl), None)

def _find_forum(guild: discord.Guild, name_norm: str) -> Optional[discord.ForumChannel]:
    nl = name_norm
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


# =========================
# Layout loading / snapshot
# =========================
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

    path = os.getenv("LOCAL_LATEST_CONFIG", "latest_config.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _snapshot_guild(guild: discord.Guild) -> Dict[str, Any]:
    """Build a layout dict from the live guild."""
    # Roles: skip @everyone and managed
    roles = []
    for r in sorted(guild.roles, key=lambda x: x.position, reverse=True):
        if r.is_default() or r.managed:
            continue
        roles.append({"name": r.name, "color": f"#{r.colour.value:06x}"})

    # Categories
    categories = [c.name for c in sorted(guild.categories, key=lambda x: x.position)]

    # Channels
    channels = []
    for ch in sorted(guild.text_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({"name": ch.name, "type": "text", "category": ch.category.name if ch.category else ""})
    for ch in sorted(guild.voice_channels, key=lambda x: (x.category.position if x.category else -1, x.position)):
        channels.append({"name": ch.name, "type": "voice", "category": ch.category.name if ch.category else ""})
    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in sorted(forums, key=lambda x: (x.category.position if x.category else -1, getattr(x, "position", 0))):
        channels.append({"name": ch.name, "type": "forum", "category": ch.category.name if ch.category else ""})

    return {"mode": "update", "roles": roles, "categories": categories, "channels": channels}


# =========================
# Rename helpers
# =========================
async def _apply_role_renames(guild: discord.Guild, renames: List[dict]):
    by_name = { _norm(r.name): r for r in guild.roles }
    print("[Messiah] _apply_role_renames:", renames or [])
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst: 
            continue
        role = by_name.get(src)
        if role and not role.managed and not role.is_default():
            print(f"[Messiah] rename role: {role.name} -> {dst}")
            try:
                await role.edit(name=dst, reason="Messiah rename (layout)")
            except Exception as e:
                print(f"[Messiah] role rename failed {getattr(role,'name',src)} -> {dst}: {e}")

async def _apply_category_renames(guild: discord.Guild, renames: List[dict]):
    by_name = { _norm(c.name): c for c in guild.categories }
    print("[Messiah] _apply_category_renames:", renames or [])
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst:
            continue
        cat = by_name.get(src)
        if cat:
            print(f"[Messiah] rename category: {cat.name} -> {dst}")
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
    print("[Messiah] _apply_channel_renames:", renames or [])
    for m in renames or []:
        src, dst = _norm(m.get("from")), (m.get("to") or "").strip()
        if not src or not dst:
            continue
        ch = by_name.get(src)
        if ch:
            print(f"[Messiah] rename channel: {ch.name} -> {dst}")
            try:
                await ch.edit(name=dst, reason="Messiah rename (layout)")
            except Exception as e:
                print(f"[Messiah] channel rename failed {getattr(ch,'name',src)} -> {dst}: {e}")


# =========================
# Explicit DELETE helpers
# =========================
async def _delete_roles(guild: discord.Guild, names: List[str]):
    wanted: Set[str] = { _norm(n) for n in (names or []) if n }
    print("[Messiah] delete_roles ->", wanted)
    if not wanted:
        return
    for r in guild.roles:
        if r.is_default() or r.managed:
            continue
        if _norm(r.name) in wanted:
            print("[Messiah] delete role:", r.name)
            try:
                await r.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _delete_channels(guild: discord.Guild, items: List[dict]):
    # items: [{name,type,category}]
    def cat_name(ch): return ch.category.name if getattr(ch, "category", None) else ""
    wanted: Set[Tuple[str,str,str]] = set()
    for it in (items or []):
        nm = _norm(it.get("name",""))
        tp = (it.get("type") or "text").lower()
        cat = _norm(it.get("category",""))
        if nm:
            wanted.add((nm,tp,cat))
    print("[Messiah] delete_channels keys:", wanted)
    if not wanted:
        return

    # text
    for ch in list(guild.text_channels):
        key = (_norm(ch.name), "text", _norm(cat_name(ch)))
        if key in wanted:
            print("[Messiah] delete text:", ch.name, "cat:", cat_name(ch))
            try:
                await ch.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] text delete failed {ch.name}: {e}")

    # voice
    for ch in list(guild.voice_channels):
        key = (_norm(ch.name), "voice", _norm(cat_name(ch)))
        if key in wanted:
            print("[Messiah] delete voice:", ch.name, "cat:", cat_name(ch))
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
            print("[Messiah] delete forum:", ch.name, "cat:", cat_name(ch))
            try:
                await ch.delete(reason="Messiah explicit delete (layout)")
            except Exception as e:
                print(f"[Messiah] forum delete failed {ch.name}: {e}")

async def _delete_categories_with_reassign(guild: discord.Guild, items: List[dict]):
    # items: [{"name": str, "reassign_to": str|""}]
    by_name = { _norm(c.name): c for c in guild.categories }
    for it in (items or []):
        name = _norm(it.get("name",""))
        target_name = _norm(it.get("reassign_to",""))
        cat = by_name.get(name)
        target_cat = by_name.get(target_name) if target_name else None
        print("[Messiah] delete_category:", name or "(missing)", "reassign_to:", target_name or "(none)")

        if not cat:
            continue

        # Move channels first (if any)
        for ch in list(cat.channels):
            try:
                await ch.edit(category=target_cat, reason="Messiah reassign before category delete")
                print(f"[Messiah]  moved channel {getattr(ch,'name','?')} → {getattr(target_cat,'name','(no category)')}")
            except Exception as e:
                print(f"[Messiah]  failed to reassign channel {getattr(ch,'name','?')} from {cat.name}: {e}")

        # Delete category (now should be empty)
        try:
            await cat.delete(reason="Messiah explicit delete (layout)")
            print(f"[Messiah]  deleted category {getattr(cat,'name','(unknown)')}")
        except Exception as e:
            print(f"[Messiah] category delete failed {getattr(cat,'name','?')}: {e}")


# =========================
# PRUNE helpers (global)
# =========================
async def _prune_roles(guild: discord.Guild, desired_names: Set[str]):
    print("[Messiah] PRUNE roles keep set:", desired_names)
    for r in guild.roles:
        if r.is_default() or r.managed:
            continue
        if _norm(r.name) not in desired_names:
            print("[Messiah] prune role:", r.name)
            try:
                await r.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] role delete failed {r.name}: {e}")

async def _prune_categories(guild: discord.Guild, desired_names: Set[str]):
    print("[Messiah] PRUNE categories keep set:", desired_names)
    for c in guild.categories:
        if _norm(c.name) not in desired_names:
            if len(c.channels) == 0:
                print("[Messiah] prune category:", c.name)
                try:
                    await c.delete(reason="Messiah prune (not in layout)")
                except Exception as e:
                    print(f"[Messiah] category delete failed {c.name}: {e}")

async def _prune_channels(guild: discord.Guild, desired_triplets: Set[Tuple[str,str,str]]):
    print("[Messiah] PRUNE channels keep keys:", desired_triplets)
    def cat_name(ch): return ch.category.name if getattr(ch, "category", None) else ""

    for ch in list(guild.text_channels):
        key = (_norm(ch.name), "text", _norm(cat_name(ch)))
        if key not in desired_triplets:
            print("[Messiah] prune text:", ch.name)
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] text delete failed {ch.name}: {e}")

    for ch in list(guild.voice_channels):
        key = (_norm(ch.name), "voice", _norm(cat_name(ch)))
        if key not in desired_triplets:
            print("[Messiah] prune voice:", ch.name)
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] voice delete failed {ch.name}: {e}")

    try:
        forums = list(guild.forums)
    except Exception:
        forums = []
    for ch in forums:
        key = (_norm(ch.name), "forum", _norm(cat_name(ch)))
        if key not in desired_triplets:
            print("[Messiah] prune forum:", ch.name)
            try:
                await ch.delete(reason="Messiah prune (not in layout)")
            except Exception as e:
                print(f"[Messiah] forum delete failed {ch.name}: {e}")


# =========================
# Filter for UPSERT (avoid respawn)
# =========================
def _filtered_for_upsert(layout: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of layout with items marked for explicit deletion removed,
    so upsert doesn't recreate them."""
    out = {
        "mode": layout.get("mode"),
        "roles": list(layout.get("roles") or []),
        "categories": list(layout.get("categories") or []),
        "channels": list(layout.get("channels") or []),
        "renames": layout.get("renames") or {},
        "deletions": layout.get("deletions") or {},
        "prune": layout.get("prune") or {},
    }

    dels = out["deletions"]

    # Roles
    del_role_names = { _norm(n) for n in (dels.get("roles") or []) if n }
    if del_role_names:
        out["roles"] = [r for r in out["roles"] if _norm(r.get("name")) not in del_role_names]

    # Categories
    del_cat_names = { _norm(d.get("name","")) for d in (dels.get("categories") or []) if d.get("name") }
    if del_cat_names:
        out["categories"] = [c for c in out["categories"] if _norm(c) not in del_cat_names]
        out["channels"] = [ch for ch in out["channels"] if _norm(ch.get("category")) not in del_cat_names]

    # Channels
    del_chan_keys: Set[Tuple[str,str,str]] = set()
    for d in (dels.get("channels") or []):
        nm = _norm(d.get("name",""))
        tp = (d.get("type") or "text").lower()
        cat = _norm(d.get("category",""))
        if nm:
            del_chan_keys.add((nm,tp,cat))

    if del_chan_keys:
        kept = []
        for ch in out["channels"]:
            key = (_norm(ch.get("name","")), (ch.get("type") or "text").lower(), _norm(ch.get("category","")))
            if key not in del_chan_keys:
                kept.append(ch)
        out["channels"] = kept

    return out


# =========================
# COG
# =========================
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

        print("[Messiah] build_server: START guild=", interaction.guild.id)
        print("[Messiah] build_server: layout keys:", list((layout or {}).keys()))

        # Renames first (safe if empty)
        ren = (layout or {}).get("renames", {})
        print("[Messiah] stage: RENAMES")
        await _apply_role_renames(interaction.guild, ren.get("roles") or [])
        await _apply_category_renames(interaction.guild, ren.get("categories") or [])
        await _apply_channel_renames(interaction.guild, ren.get("channels") or [])

        # Upsert everything (no deletes in build)
        print("[Messiah] stage: UPSERT (build)")
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

        print("[Messiah] update_server: START guild=", interaction.guild.id)
        print("[Messiah] update_server: layout keys:", list((layout or {}).keys()))
        print("[Messiah] update_server: renames:", (layout or {}).get("renames"))
        print("[Messiah] update_server: deletions:", (layout or {}).get("deletions"))
        print("[Messiah] update_server: prune:", (layout or {}).get("prune"))

        # 1) RENAMES (first)
        print("[Messiah] stage: RENAMES")
        ren = (layout or {}).get("renames", {})
        await _apply_role_renames(interaction.guild, ren.get("roles") or [])
        await _apply_category_renames(interaction.guild, ren.get("categories") or [])
        await _apply_channel_renames(interaction.guild, ren.get("channels") or [])

        # 2) EXPLICIT DELETES (before upsert to avoid respawn)
        print("[Messiah] stage: EXPLICIT DELETES")
        dels = (layout.get("deletions") or {})
        await _delete_channels(interaction.guild, dels.get("channels") or [])
        await _delete_categories_with_reassign(interaction.guild, dels.get("categories") or [])
        await _delete_roles(interaction.guild, dels.get("roles") or [])

        # 3) UPSERT on FILTERED layout
        filtered = _filtered_for_upsert(layout)
        print("[Messiah] stage: UPSERT (filtered sizes)",
              len(filtered.get("roles",[])),
              len(filtered.get("categories",[])),
              len(filtered.get("channels",[])))
        await self._apply_layout(interaction.guild, filtered, update_only=True)

        # 4) Optional PRUNE of anything else not listed
        prune = (layout.get("prune") or {})
        print("[Messiah] stage: PRUNE flags", prune)
        if prune.get("roles"):
            wanted_roles = { _norm(r.get("name","")) for r in (filtered.get("roles") or []) if r.get("name") }
            await _prune_roles(interaction.guild, wanted_roles)
        if prune.get("categories"):
            wanted_cats = { _norm(n) for n in (filtered.get("categories") or []) if n }
            await _prune_categories(interaction.guild, wanted_cats)
        if prune.get("channels"):
            wanted_triplets: Set[Tuple[str,str,str]] = set()
            for ch in (filtered.get("channels") or []):
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
            name_n = _norm(raw_name)
            if not name_n:
                continue
            color = _hex_to_color(r.get("color"))
            existing = _find_role(guild, name_n)
            if existing is None:
                try:
                    await guild.create_role(name=raw_name, color=color, reason="MessiahBot builder")
                    logs.append(f"✅ Role created: **{raw_name}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create role: **{raw_name}**")
            else:
                try:
                    await existing.edit(colour=color, reason="MessiahBot update role color")
                    logs.append(f"🔄 Role updated color: **{existing.name}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to edit role: **{existing.name}**")

        # CATEGORIES
        cat_cache: Dict[str, discord.CategoryChannel] = {}
        for cname in layout.get("categories", []):
            cname_n = _norm(cname)
            if not cname_n:
                continue
            cat = _find_category(guild, cname_n)
            if cat is None:
                try:
                    cat = await guild.create_category(cname, reason="MessiahBot builder")
                    logs.append(f"✅ Category created: **{cname}**")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create category: **{cname}**")
            else:
                logs.append(f"⏭️ Category exists: **{cat.name}**")
            if cat:
                cat_cache[cname_n] = cat

        # CHANNELS
        for ch in layout.get("channels", []):
            chname_raw = ch.get("name")
            chname = _norm(chname_raw)
            chtype = (_norm(ch.get("type")) or "text")
            catname = _norm(ch.get("category"))
            if not chname:
                continue

            parent = None
            if catname:
                parent = _find_category(guild, catname) or cat_cache.get(catname)
                if parent is None:
                    try:
                        parent = await guild.create_category(ch.get("category"), reason="MessiahBot builder (parent for channel)")
                        logs.append(f"✅ Category created for parent: **{ch.get('category')}**")
                        cat_cache[catname] = parent
                    except discord.Forbidden:
                        logs.append(f"❌ Missing permission to create category: **{ch.get('category')}**")

            # Re-check existence right before create (helps after deletes)
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
                        await guild.create_text_channel(chname_raw, category=parent, reason="MessiahBot builder")
                    elif chtype == "voice":
                        await guild.create_voice_channel(chname_raw, category=parent, reason="MessiahBot builder")
                    elif chtype == "forum":
                        create_forum = getattr(guild, "create_forum", None) or getattr(guild, "create_forum_channel", None)
                        if create_forum:
                            await create_forum(name=chname_raw, category=parent, reason="MessiahBot builder")
                        else:
                            raise RuntimeError("This discord.py version lacks forum creation API")
                    logs.append(f"✅ Channel created: **#{chname_raw}** [{chtype}]{' → ' + parent.name if parent else ''}")
                except discord.Forbidden:
                    logs.append(f"❌ Missing permission to create channel: **{chname_raw}**")
                except Exception as e:
                    logs.append(f"❌ Failed to create channel **{chname_raw}**: {e}")
            else:
                try:
                    need_parent_id = parent.id if parent else None
                    has_parent_id = existing.category.id if getattr(existing, "category", None) else None
                    if need_parent_id != has_parent_id:
                        await existing.edit(category=parent, reason="MessiahBot move to correct category")
                        logs.append(f"🔀 Moved **#{existing.name}** → **{parent.name if parent else 'no category'}**")
                    else:
                        logs.append(f"⏭️ Channel exists & placed: **#{existing.name}**")
                except discord.Forbidden:
                    logs.append(f"⚠️ No permission to move channel: **{chname_raw}**")

        if logs:
            print(f"[MessiahBot Builder] {guild.name}:\n - " + "\n - ".join(logs))


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))
