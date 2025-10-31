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
    scope = "channel:read:stream_schedule channel:manage:stream_schedule user:read:email"
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

        # Save Twitch account and tokens
    async with await psycopg.AsyncConnection.connect(DATABASE_URL, sslmode="require") as conn:
        async with conn.cursor() as cur:
            # Ensure the user exists in bot_users
            await cur.execute("""
                INSERT INTO bot_users (discord_id, twitch_id, premium)
                VALUES (%s, %s, false)
                ON CONFLICT (discord_id) DO UPDATE SET twitch_id = EXCLUDED.twitch_id
                RETURNING id
            """, (
                session.get("discord_user", {}).get("id"),
                str(twitch_user["id"])
            ))
            bot_user = await cur.fetchone()

            # Store Twitch token info in twitch_tokens
            await cur.execute("""
                INSERT INTO twitch_tokens (
                    user_id, access_token, refresh_token, scope,
                    token_obtained_at, token_expires_at, broadcaster_id, login
                )
                VALUES (
                    %s, %s, %s, %s, NOW(),
                    NOW() + interval '%s seconds',
                    %s, %s
                )
                ON CONFLICT (user_id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    token_expires_at = EXCLUDED.token_expires_at
            """, (
                bot_user[0],
                token["access_token"],
                token.get("refresh_token"),
                ["channel:read:stream_schedule", "channel:manage:stream_schedule," "user:read:email"],
                token.get("expires_in", 3600),
                twitch_user["id"],
                twitch_user["login"]
            ))

        await conn.commit()

    return f"""
        ✅ Connected Twitch account: {twitch_user['display_name']}<br>
        Linked to Discord guild: {guild_id}<br><br>
        <a href='/dashboard'>Return to Dashboard</a>
    """