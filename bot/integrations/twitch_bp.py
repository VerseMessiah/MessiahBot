# bot/twitch_bp.py
import os
import psycopg
from psycopg.rows import dict_row
from flask import Blueprint, redirect, request, session
import requests

twitch_bp = Blueprint("twitch_bp", __name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")

TWITCH_SCOPE = "user:read:email"


@twitch_bp.route("/oauth/start/<guild_id>")
def twitch_oauth_start(guild_id):
    """Start the Twitch OAuth flow"""
    scope = TWITCH_SCOPE
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
    code = request.args.get("code")
    guild_id = request.args.get("state")

    if not code:
        return "❌ Missing OAuth code from Twitch", 400

    token_data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TWITCH_REDIRECT_URI,
    }

    # --- Exchange code for token ---
    token_res = requests.post("https://id.twitch.tv/oauth2/token", data=token_data, timeout=20)
    try:
        token = token_res.json()
    except Exception:
        token = {"parse_error": token_res.text}

    if token_res.status_code != 200 or "access_token" not in token:
        return (
            f"❌ Twitch OAuth failed<br><br>"
            f"Redirect URI must be:<br><code>{TWITCH_REDIRECT_URI}</code><br><br>"
            f"Raw response ({token_res.status_code}):<br><pre>{token}</pre>",
            400,
        )

    # --- Fetch Twitch user info ---
    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Client-Id": TWITCH_CLIENT_ID
    }
    user_res = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=20)
    try:
        user_data = user_res.json()
    except Exception:
        user_data = {"parse_error": user_res.text}

    if user_res.status_code != 200 or not user_data.get("data"):
        return (
            f"❌ Failed to fetch Twitch user info "
            f"({user_res.status_code}):<br><pre>{user_data}</pre>",
            400,
        )
    twitch_user = user_data["data"][0]

    # --- Save to DB (sync psycopg) ---
    try:
        with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                print(f"💾 Saving token for guild {guild_id}, user {twitch_user['id']}")
                cur.execute(
                    """
                    INSERT INTO twitch_tokens (
                        guild_id, twitch_user_id, access_token, refresh_token, expires_at, scope
                    )
                    VALUES (%s, %s, %s, %s, NOW() + interval '60 minutes', %s)
                    ON CONFLICT (guild_id, twitch_user_id)
                    DO UPDATE SET
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        expires_at = EXCLUDED.expires_at,
                        scope = EXCLUDED.scope,
                        updated_at = NOW();
                    """,
                    (
                        str(guild_id) if guild_id is not None else None,
                        str(twitch_user["id"]),
                        str(token.get("access_token", "")),
                        str(token.get("refresh_token", "")),
                        TWITCH_SCOPE,
                    ),
                )
        msg = (
            f"✅ Connected Twitch account: <b>{twitch_user['display_name']}</b><br>"
            f"Linked to Discord guild: {guild_id or '(missing)'}<br><br>"
            f"<a href='/dashboard'>Return to Dashboard</a>"
        )
        return msg
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Database error while saving Twitch token:<br><pre>{e}</pre>", 500