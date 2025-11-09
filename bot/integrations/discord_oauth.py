import os
from flask import Blueprint, redirect, request, session, url_for
import aiohttp, psycopg
from psycopg.rows import dict_row

discord_bp = Blueprint("discord_bp", __name__)

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")

@discord_bp.route("/api/discord/oauth/start")
def discord_oauth_start():
    scope = "identify email guilds"
    url = (
        f"https://discord.com/api/oauth2/authorize?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code&scope={scope}"
    )
    return redirect(url)

@discord_bp.route("/api/discord/oauth/callback")
async def discord_oauth_callback():
    code = request.args.get("code")

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

        headers = {"Authorization": f"Bearer {token['access_token']}"}
        async with http.get("https://discord.com/api/users/@me", headers=headers) as r:
            user = await r.json()
        async with http.get("https://discord.com/api/users/@me/guilds", headers=headers) as r:
            guilds = await r.json()

    # Save user info
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO bot_users (discord_id, premium)
                VALUES (%s, false)
                ON CONFLICT (discord_id) DO NOTHING
            """, (str(user["id"]),))
        await conn.commit()

    # Store in session
    session["discord_user"] = user
    session["guilds"] = guilds

    guild_names = [g["name"] for g in guilds]
    return f"""
        ✅ Logged in as {user['username']}#{user['discriminator']}<br>
        Accessible guilds:<br>
        {', '.join(guild_names)}
        <br><br>
        <a href='/dashboard'>Continue to Dashboard</a>
    """