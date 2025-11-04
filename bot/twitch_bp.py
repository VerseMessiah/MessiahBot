# bot/twitch_bp.py
import os
import psycopg
from flask import Blueprint, redirect, request, session
import requests

twitch_bp = Blueprint("twitch_bp", __name__, url_prefix="/api/twitch")

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")


@twitch_bp.route("/oauth/start/<guild_id>")
def twitch_oauth_start(guild_id):
    """Start the Twitch OAuth flow"""
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


@twitch_bp.route("/oauth/callback")
def twitch_oauth_callback():
    """Handle the OAuth redirect and store the access token"""
    code = request.args.get("code")
    guild_id = request.args.get("state")

    if not code:
        return (
            "<h3>❌ Missing authorization code</h3>"
            "<p>Try starting the OAuth flow again.</p>",
            400,
        )

    token_data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TWITCH_REDIRECT_URI,
    }

    # Step 1 — Exchange authorization code for access token
    token_resp = requests.post("https://id.twitch.tv/oauth2/token", data=token_data)
    token = token_resp.json()

    if "access_token" not in token:
        return (
            f"<h3>❌ Twitch OAuth failed</h3>"
            f"<p>No access_token found in response. Check your Twitch app redirect URI:</p>"
            f"<pre>{TWITCH_REDIRECT_URI}</pre>"
            f"<p>Raw response:</p><pre>{token}</pre>",
            400,
        )

    access_token = token["access_token"]
    refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in")

    # Step 2 — Get Twitch user info
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": TWITCH_CLIENT_ID,
    }
    user_resp = requests.get("https://api.twitch.tv/helix/users", headers=headers)
    user_data = user_resp.json()

    if "data" not in user_data or not user_data["data"]:
        return f"<h3>❌ Failed to fetch Twitch user info</h3><pre>{user_data}</pre>", 400

    twitch_user = user_data["data"][0]

    # Step 3 — Save or update in database
    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO twitch_tokens (guild_id, twitch_user_id, access_token, refresh_token, expires_in)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (guild_id)
                DO UPDATE SET
                    twitch_user_id = EXCLUDED.twitch_user_id,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_in = EXCLUDED.expires_in
                """,
                (
                    guild_id,
                    str(twitch_user["id"]),
                    access_token,
                    refresh_token,
                    expires_in,
                ),
            )
        conn.commit()

    session["twitch_user"] = twitch_user

    return f"""
        ✅ Connected Twitch account: {twitch_user['display_name']}<br>
        Linked to Discord guild: {guild_id or '(none)'}<br><br>
        <a href='/form'>Return to Dashboard</a>
    """