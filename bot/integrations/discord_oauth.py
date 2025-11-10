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
        f"https://discord.com/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return redirect(url)

# ------------------------------------------------------
# 2️⃣ Discord OAuth Callback
# ------------------------------------------------------
@discord_bp.route("/oauth/discord/callback")
def discord_oauth_callback():
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

    t = requests.post("https://discord.com/oauth2/token", data=token_data, timeout=20)
    if t.status_code != 200:
        return f"Token exchange failed {t.status_code} {t.text}", 400
    token = t.json()
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    u = requests.get("https://discord.com/api/users/@me", headers=headers, timeout=20)
    g = requests.get("https://discord.com/api/users/@me/guilds", headers=headers, timeout=20)
    if u.status_code != 200:
        return f"Fetch user failed: {u.status_code} {u.text}", 400
    if g.status_code != 200:
        return f"Fetch guilds failed: {g.status_code} {g.text}", 400
    
    user = u.json()
    guilds = g.json()

    try:
        with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
            with conn.curson(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    INSERT INTO bot_users (discord_id, premium)
                       VALUES (%s, false)
                       ON CONFLICT (discord_id) DO NOTHING
                    """,
                    (user["id"],),
                    )           
    
    except Exception as e:
        return f"DB error: {e}", 500
    
    session["discord_user"] = {
        "id": str(user["id"]),
        "username": user.get("username"),
        "discriminator": user.get("discriminator"),
        "avatar": user.get("avatar"),
    }
    session["guilds"] = guilds

    return redirect(url_for("index"))