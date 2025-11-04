import os
from flask import Blueprint, redirect, request, session
import requests, psycopg

twitch_bp = Blueprint("twitch_bp", __name__)

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TWITCH_REDIRECT_URI = os.getenv("TWITCH_REDIRECT_URI")
DATABASE_URL = os.getenv("DATABASE_URL")


@twitch_bp.route("/api/twitch/oauth/callback")
def twitch_oauth_callback():
    import requests  # make sure requests is imported

    code = request.args.get("code")
    guild_id = request.args.get("state")

    token_data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": TWITCH_REDIRECT_URI,
    }

    # Step 1: Exchange authorization code for access token
    token_resp = requests.post("https://id.twitch.tv/oauth2/token", data=token_data)
    token = token_resp.json()

    # --- Safety check ---
    if "access_token" not in token:
        return (
            f"<h3>❌ Twitch OAuth failed</h3>"
            f"<p>No access_token found in response. This means the authorization failed.</p>"
            f"<pre>{token}</pre>"
            f"<p>Check that your Twitch app’s redirect URI exactly matches:<br>"
            f"<code>{TWITCH_REDIRECT_URI}</code></p>",
            400,
        )

    access_token = token["access_token"]
    refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in")

    # Step 2: Get Twitch user info
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": TWITCH_CLIENT_ID,
    }
    user_resp = requests.get("https://api.twitch.tv/helix/users", headers=headers)
    user_data = user_resp.json()

    if "data" not in user_data or not user_data["data"]:
        return f"<h3>❌ Failed to fetch Twitch user info</h3><pre>{user_data}</pre>", 400

    twitch_user = user_data["data"][0]

    # Step 3: Save or update tokens in database
    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO twitch_tokens (guild_id, twitch_user_id, access_token, refresh_token, expires_in)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (guild_id)
                DO UPDATE SET
                    twitch_user_id = EXCLUDED.twitch_user_id,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_in = EXCLUDED.expires_in
            """, (
                guild_id,
                str(twitch_user["id"]),
                access_token,
                refresh_token,
                expires_in,
            ))
        conn.commit()

    session["twitch_user"] = twitch_user

    return f"""
        ✅ Connected Twitch account: {twitch_user['display_name']}<br>
        Linked to Discord guild: {guild_id or '(none)'}<br><br>
        <a href='/dashboard'>Return to Dashboard</a>
    """
