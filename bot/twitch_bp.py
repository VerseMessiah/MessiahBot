import os
from flask import Blueprint, redirect, request, session
import aiohttp, psycopg
from psycopg.rows import dict_row

twitch_bp = Blueprint("twitch_bp", __name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")


@twitch_bp.route("/api/twitch/oauth/start/<guild_id>")
def twitch_oauth_start(guild_id):
    scope = "channel:read:schedule user:read:email"
    url = (
        f"https://id.twitch.tv/oauth2/authorize"
        f"?client_id={TWITCH_CLIENT_ID}"
        f"&redirect_uri={TWITCH_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&state={guild_id}"
    )
    return redirect(url)


@twitch_bp.route("/api/twitch/oauth/callback")
async def twitch_oauth_callback():
    code = request.args.get("code")
    guild_id = request.args.get("state")

    token_data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TWITCH_REDIRECT_URI,
    }

    async with aiohttp.ClientSession() as http:
        async with http.post("https://id.twitch.tv/oauth2/token", data=token_data) as r:
            token = await r.json()

        headers = {"Authorization": f"Bearer {token['access_token']}", "Client-Id": TWITCH_CLIENT_ID}
        async with http.get("https://api.twitch.tv/helix/users", headers=headers) as r:
            user_data = await r.json()

    twitch_user = user_data["data"][0]

    # Save Twitch account
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE bot_users
                SET twitch_id = %s
                WHERE discord_id IS NOT NULL AND discord_id = %s
                RETURNING id
            """, (str(twitch_user["id"]), session.get("discord_user", {}).get("id")))
            if cur.rowcount == 0:
                await cur.execute("""
                    INSERT INTO bot_users (twitch_id, premium)
                    VALUES (%s, false)
                    ON CONFLICT (twitch_id) DO NOTHING
                """, (str(twitch_user["id"]),))
        await conn.commit()

    session["twitch_user"] = twitch_user

    return f"""
        ✅ Connected Twitch account: {twitch_user['display_name']}<br>
        Linked to Discord guild: {guild_id}<br><br>
        <a href='/dashboard'>Return to Dashboard</a>
    """