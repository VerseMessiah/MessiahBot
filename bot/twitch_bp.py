import os
from flask import Blueprint, redirect, request, session
import requests, psycopg

twitch_bp = Blueprint("twitch_bp", __name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")


@twitch_bp.route("/api/twitch/oauth/start/<guild_id>")
def twitch_oauth_start(guild_id):
    # Twitch has deprecated channel:read:schedule and channel:read:stream_schedule
    # Keep only user:read:email for now (identity + linking)
    scope = "user:read:email"
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
def twitch_oauth_callback():
    code = request.args.get("code")
    guild_id = request.args.get("state")

    token_data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TWITCH_REDIRECT_URI,
    }

    token = requests.post("https://id.twitch.tv/oauth2/token", data=token_data).json()

    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Client-Id": TWITCH_CLIENT_ID,
    }
    user_data = requests.get("https://api.twitch.tv/helix/users", headers=headers).json()
    twitch_user = user_data["data"][0]

    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor() as cur:
            # Link Twitch ID with Discord if available in session
            discord_user = session.get("discord_user")
            if discord_user:
                cur.execute("""
                    INSERT INTO bot_users (discord_id, twitch_id, premium)
                    VALUES (%s, %s, false)
                    ON CONFLICT (discord_id)
                    DO UPDATE SET twitch_id = EXCLUDED.twitch_id
                """, (str(discord_user["id"]), str(twitch_user["id"])))
            else:
                cur.execute("""
                    INSERT INTO bot_users (twitch_id, premium)
                    VALUES (%s, false)
                    ON CONFLICT (twitch_id) DO NOTHING
                """, (str(twitch_user["id"]),))
        conn.commit()

    session["twitch_user"] = twitch_user

    return f"""
        ✅ Connected Twitch account: {twitch_user['display_name']}<br>
        Linked to Discord guild: {guild_id or '(none)'}<br><br>
        <a href='/dashboard'>Return to Dashboard</a>
    """
