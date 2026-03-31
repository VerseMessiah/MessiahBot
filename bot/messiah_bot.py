# bot/messiah_bot.py
"""
MessiahBot main Discord service
--------------------------------
Responsible for:
 - Connecting to Discord
 - Loading all command cogs (server builder, Plex, schedule sync, etc.)
 - Syncing slash commands
"""

import os
import asyncio
import discord
from discord.ext import commands
from dotenv import load_dotenv
from discord import Guild
import json
import hashlib
from bot.integrations.db import init_db_pool, fetch_one, execute

print("🧠 MessiahBot module loaded")

# Load environment
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Intents setup
INTENTS = discord.Intents.all()
INTENTS.guilds = True
INTENTS.members = True
INTENTS.guild_scheduled_events = True
INTENTS.message_content = True

def _slash_signature(bot: commands.Bot) -> str:
    """
    Build a stable JSON signature of the app commands currently registered.
    We only include fields that reflect actual command structure.
    """
    def cmd_to_dict(c: discord.app_commands.Command) -> dict:
        return {
            "name": c.name,
            "description": getattr(c, "description", "") or "",
            "type": int(getattr(c, "type", 1)),
            "options": [
                {
                    "name": o.name,
                    "description": getattr(o, "description", "") or "",
                    "required": getattr(o, "required", False),
                    "type": int(getattr(o, "type", 3)),
                    "choices": [getattr(ch, "name", str(ch)) for ch in (getattr(o, "choices", None) or [])],
                }
                for o in (getattr(c, "parameters", None) or [])
            ],
        }

    cmds = bot.tree.get_commands()
    payload = {"commands": sorted([cmd_to_dict(c) for c in cmds], key=lambda x: x["name"])}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _slash_hash(bot: commands.Bot) -> str:
    sig = _slash_signature(bot).encode("utf-8")
    return hashlib.sha256(sig).hexdigest()

class MessiahBot(commands.Bot):
    """Primary bot instance for Discord service"""

    def __init__(self):
        super().__init__(
            command_prefix="!",  # for legacy commands
            intents=INTENTS,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def setup_hook(self):
        """Load all Cogs and sync slash commands."""
        print("🚀 setup_hook triggered (loading extensions)")

        # Initialize shared DB pool once at startup.
        # If DB isn't configured in this environment, we still let the bot start,
        # but we'll skip DB-dependent cogs.
        db_ready = True
        try:
            await init_db_pool()
            print("✅ DB pool initialized")
        except Exception as e:
            db_ready = False
            print(f"❌ DB pool init failed (DB-dependent features disabled): {type(e).__name__}: {e}")

        # All Discord-side Cogs go here
        extensions = [
            "bot.commands.server_builder",
            "bot.commands.plex_commands",
        ]
        if db_ready:
            extensions.append("bot.commands.schedule_sync")
        else:
            print("⚠️ Skipping bot.commands.schedule_sync because DB is not initialized")

        for ext in extensions:
            try:
                await self.load_extension(ext)
                print(f"✅ Loaded extension: {ext}")
            except Exception as e:
                print(f"❌ Failed to load {ext}: {type(e).__name__}: {e}")

        # Sync slash commands (global)
        try:
            await self.tree.sync()
            print("✅ Slash commands synced globally")
        except Exception as e:
            print(f"❌ Slash sync error: {e}")
    
    
# Instantiate bot
bot = MessiahBot()

async def debug_events(guild: Guild):
    events = await guild.fetch_scheduled_events()

    for ev in events:
        print("EVENT:", ev.name)
        print("  entity_type:", ev.entity_type)
        print("  has location attr:", hasattr(ev, "location"))
        print("  location value:", ev.location)


# Global error handler for slash/app commands
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    print(f"[MessiahBot] app command error: {type(error).__name__}: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Something went wrong running that command.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Something went wrong running that command.", ephemeral=True)
    except Exception:
        pass

# Global error handler for prefix commands (e.g., !debug_twitch)
@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    print(f"[MessiahBot] command error: {type(error).__name__}: {error}")
    try:
        await ctx.send(f"❌ Command error: {type(error).__name__}: {error}")
    except Exception:
        pass

@bot.event
async def on_ready():
    user = bot.user
    if user is None:
        print("✨ MessiahBot is online (bot.user is not available yet)")
    else:
        print(f"✨ MessiahBot is online as {user} (ID: {user.id})")

    guild = bot.get_guild(1408900348671824024)
    if guild is None:
        print("⚠️ Test guild not found in cache; skipping debug_events.")
        return
    await debug_events(guild)

@bot.command(name="syncslash")
@commands.guild_only()
async def syncslash(ctx: commands.Context):
    owner_id = os.getenv("ADMIN_DISCORD_ID")
    if not owner_id or str(ctx.author.id) != str(owner_id):
        await ctx.send("❌ Not authorized.")
        return

    await ctx.send("⏳ Syncing slash commands…")
    try:
        await bot.tree.sync()
        h = _slash_hash(bot)

        await execute(
            """
            INSERT INTO app_kv (key, value) VALUES (%s, %s)
            ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()
            """,
            ("slash_hash", h),
        )
        await ctx.send("✅ Slash commands synced.")
    except Exception as e:
        await ctx.send(f"❌ Sync failed: {type(e).__name__}: {e}")


# Entrypoint (run from repo root: python -m bot.messiah_bot)
async def _run_bot_with_backoff():
    token = DISCORD_BOT_TOKEN
    if not token:
        raise SystemExit("❌ Missing DISCORD_BOT_TOKEN")

    backoff = 60  # seconds
    while True:
        try:
            print("[MessiahBot] Starting Discord client…")
            await bot.start(token)
            print("[MessiahBot] bot.start() returned; exiting.")
            return
        except discord.HTTPException as e:
            status = getattr(e, "status", None)
            if status == 429:
                print(f"[MessiahBot] Login rate limited (HTTP 429). Sleeping {backoff}s before retry…")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900)  # cap at 15 minutes
                continue
            raise
        except Exception as e:
            print(f"[MessiahBot] Startup error: {type(e).__name__}: {e}. Sleeping 30s before retry…")
            await asyncio.sleep(30)
            backoff = min(backoff * 2, 900)
            continue
        finally:
            try:
                await bot.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(_run_bot_with_backoff())
