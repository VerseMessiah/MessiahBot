# bot/integrations/discord_oauth.py
import os
from flask import Blueprint, redirect, request, session
import aiohttp
import psycopg
from psycopg.rows import dict_row

# 🔹 Blueprint setup
discord_bp = Blueprint("discord_bp", __name__)

# 🔹 Environment variables
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")

# ------------------------------------------------------
# 1️⃣ Discord OAuth Start (renamed from /api/... to /login)
# ------------------------------------------------------
@discord_bp.route("/login")
def discord_oauth_start():
    """Redirect user to Discord OAuth consent screen"""
    scope = "identify email guilds"
    url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code&scope={scope}"
    )
    return redirect(url)

# ------------------------------------------------------
# 2️⃣ Discord OAuth Callback
# ------------------------------------------------------
@discord_bp.route("/api/discord/oauth/callback")
async def discord_oauth_callback():
    """Handle OAuth callback from Discord"""
    code = request.args.get("code")
    if not code:
        return "Missing authorization code", 400

    token_data = {
        "client_id": DISCORD_CLIENT_ID,
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": DISCORD_REDIRECT_URI,
    }

    async with aiohttp.ClientSession() as http:
        async with http.post("https://discord.com/api/oauth2/token", data=token_data) as r:
            token = await r.json()

        if "access_token" not in token:
            return f"Failed to fetch token: {token}", 400

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        async with http.get("https://discord.com/api/users/@me", headers=headers) as r:
            user = await r.json()
        async with http.get("https://discord.com/api/users/@me/guilds", headers=headers) as r:
            guilds = await r.json()

    # ------------------------------------------------------
    # 3️⃣ Save user info in DB
    # ------------------------------------------------------
    try:
        async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    INSERT INTO bot_users (discord_id, premium)
                    VALUES (%s, false)
                    ON CONFLICT (discord_id) DO NOTHING
                """, (str(user["id"]),))
            await conn.commit()
    except Exception as e:
        print(f"[OAuth] Database insert failed: {e}")

    # ------------------------------------------------------
    # 4️⃣ Store in Flask session
    # ------------------------------------------------------
    session["discord_user"] = user
    session["guilds"] = guilds

    guild_names = [g["name"] for g in guilds]
    return f"""
        ✅ Logged in as {user['username']}#{user['discriminator']}<br>
        Accessible guilds:<br>
        {', '.join(guild_names)}
        <br><br>
        <a href='/form'>Continue to Dashboard</a>
    """
