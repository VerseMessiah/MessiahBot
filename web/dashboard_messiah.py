import os
import redis
from flask import Flask, render_template, jsonify, request, session
from dotenv import load_dotenv
from flask_session import Session
from datetime import timedelta

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "PRD").upper()
PORT = int(os.getenv("PORT", 5000))
REDIS_URL = os.getenv("REDIS_URL", None)
PLEX_URL = os.getenv("PLEX_URL", None)
PLEX_TOKEN = os.getenv("PLEX_TOKEN", None)
PLEX_OWNER = os.getenv("PLEX_OWNER", None)
PLEX_PLATFORM = os.getenv("PLEX_PLATFORM", None)

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(
    __name__,
    template_folder=TEMPLATE_DIR,
    static_folder=STATIC_DIR
)

app.config.update(
    SECRET_KEY=os.getenv("DISCORD_SESSION_SECRET", "fallback_secret"),

    # Session storage
    SESSION_TYPE="redis",
    SESSION_REDIS=redis.from_url(REDIS_URL),
    SESSION_USE_SIGNER=True,
    SESSION_PERMANENT=True,

    # Lifetime (7 days)
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),

    # Cookie properties
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_PATH="/",
    SESSION_COOKIE_DOMAIN=None,  # Let Flask auto-detect
)

@app.after_request
def debug_cookies(resp):
    print("[DEBUG] Set-Cookie headers:", resp.headers.getlist("Set-Cookie"))
    return resp
@app.after_request
def force_session_cookie(response):
    # Force Flask to write the session cookie every time
    try:
        session.modified = True
        app.session_interface.save_session(app, session, response)
        print("[DEBUG] Forced session cookie write.")
    except Exception as e:
        print("[DEBUG] Failed to force session cookie write:", e)
    return response

from bot.integrations.discord_oauth import discord_bp
from bot.integrations.twitch_bp import twitch_bp

app.register_blueprint(discord_bp)
app.register_blueprint(twitch_bp)

Session(app)

print(app.url_map)

@app.before_request
def make_session_permanent():
    session.permanent = True

@app.route("/")
def index():
    return render_template("index.html", env=ENVIRONMENT)

@app.route("/form")
def form():
    print("[DEBUG] Current session user:", session.get("discord_user"))
    return render_template("form.html", env=ENVIRONMENT, session=session)

@app.route("/ping")
def ping():
    return jsonify({"ok": True, "env": ENVIRONMENT})

@app.route("/whoami")
def whoami():
    return jsonify({
        "environment": ENVIRONMENT,
        "plex_url": bool(PLEX_URL),
        "plex_owner": PLEX_OWNER or "",
        "plex_platform": PLEX_PLATFORM or "",
        "redis": bool(REDIS_URL)
    })

@app.route("/whoami/guild")
def whoami_guild():
    """Return the current user's first guild (for Twitch connect links)."""
    from flask import session
    discord_user = session.get("discord_user")
    guilds = session.get("guilds", [])

    if not discord_user or not guilds:
        return {"ok": False, "error": "Not logged in via Discord"}, 401

    # pick the first guild as default (or extend later for multi-guild)
    guild_id = guilds[0].get("id") if guilds else None
    return {"ok": True, "guild_id": guild_id}


@app.route("/envcheck")
def envcheck():
    return jsonify({
        "status": "ok",
        "plex": bool(PLEX_URL and PLEX_TOKEN),
        "twitch": bool(os.getenv("TWITCH_CLIENT_ID")),
        "discord": bool(os.getenv("DISCORD_APP_CLIENT_ID"))
    })
@app.route("/sessioncheck")
def sessioncheck():
    print("[DEBUG] Current Redis URL:", REDIS_URL)
    print("[DEBUG] Session ID:", session.sid if hasattr(session, "sid") else "none")
    return jsonify({"discord_user": session.get("discord_user")})

@app.route("/redis/status")
def redis_status():
    if not REDIS_URL:
        return jsonify({"ok": False, "error": "REDIS_URL not configured"}), 500
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.set("test", "ok", ex=5)
        val = r.get("test")
        return jsonify({"ok": True, "value": val.decode("utf-8")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/plex/status")
def plex_status():
    if not PLEX_URL or not PLEX_TOKEN:
        return jsonify({"ok": False, "error": "Missing PLEX_URL or PLEX_TOKEN"}), 500
    try:
        import requests
        headers = {"X-Plex-Token": PLEX_TOKEN}
        resp = requests.get(f"{PLEX_URL}/", headers=headers, timeout=10)
        ok = resp.status_code == 200
        return jsonify({
            "ok": ok,
            "status_code": resp.status_code,
            "plex_owner": PLEX_OWNER,
            "plex_platform": PLEX_PLATFORM
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    
print("✅ Registered routes:")
for rule in app.url_map.iter_rules():
    print(" ", rule)

if __name__ == "__main__":
    print("🚀 MessiahBot Dashboard starting...")
    print(f"🌎 Environment: {ENVIRONMENT}")
    print(f"🧠 Flask templates: {app.template_folder}")
    print(f"🎨 Flask static: {app.static_folder}")
    app.run(host="0.0.0.0", port=PORT)
