import os
from flask import Flask, Blueprint, current_app, redirect, request, session, url_for, jsonify, render_template
from dotenv import load_dotenv
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

# --- Cookie-based session config (no Redis) ---
app.config.update({
    "SECRET_KEY": os.getenv("SECRET_KEY", "supersecretkey"),
    "SESSION_COOKIE_SECURE": True,     # required for HTTPS
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "None", # allow Discord OAuth redirects
    "SESSION_COOKIE_DOMAIN": None,
    "SESSION_PERMANENT": True,
    "PERMANENT_SESSION_LIFETIME": timedelta(days=7),
    "SESSION_REFRESH_EACH_REQUEST": True,
    "SESSION_COOKIE_NAME": "messiah_session",
})

# app.session_interface = None  # Reset session interface to avoid conflicts
# Session(app)

@app.before_request
def avoid_empty_session_writes():
    # If the session has not been touched, don't force a save later.
    if not session:
        session.modified = False

from bot.integrations.discord_oauth import discord_bp
from bot.integrations.twitch_bp import twitch_bp

app.register_blueprint(discord_bp)
app.register_blueprint(twitch_bp)

print(app.url_map)

@app.before_request
def make_session_permanent():
    session.permanent = True
    if session.get("discord_user") or session.get("guilds"):
        session.modified = True

def ignore_bad_icon_paths():
    """Ignore requests for common missing favicon paths."""
    path = request.path.lower()
    if 'icon' in path and not path.endswith(('.ico', '.png')):
        return "", 204

def is_session_exempt_route():
    """Check if the current request path is an exempt route that should skip session saving."""
    exempt_paths = [
        "/ping",
        "/favicon.ico",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png"
    ]
    return request.path.lower() in exempt_paths

@app.after_request
def debug_cookie_headers(response):
    """Only debug cookie headers; let Flask manage saving."""
    try:
        if is_session_exempt_route():
            print(f"[DEBUG] Skipping session save for exempt route: {request.path}")
            return response
        print("[DEBUG] Session state, discord_user present:", bool(session.get("discord_user")))
        cookies = response.headers.getlist("Set-Cookie")
        if cookies:
            print("[DEBUG] Set-Cookie headers:", cookies)
            for cookie in cookies:
                if "Expires=Thu, 01 Jan 1970" in cookie:
                    print("⚠️ Flask deleted the session cookie during this request!")
        else:
            print("[DEBUG] No Set-Cookie headers.")
    except Exception as e:
        print("[DEBUG] Cookie debug error:", e)
    return response

@app.route("/")
def index():
    return redirect("/form")
                           
@app.route("/form")
def form():
    print("[DEBUG] Current session user:", session.get("discord_user"))
    return render_template("form.html", env=ENVIRONMENT, session=session)

@app.route("/ping")
def ping():
    return jsonify({"ok": True, "env": ENVIRONMENT})

@app.route("/whoami")
def whoami():
    du = session.get("discord_user")
    guilds = session.get("guilds") or []
    return jsonify({
        "environment": ENVIRONMENT,
        "logged_in": bool(du),
        "user": {
            "id": du.get("id") if du else None,
            "username": du.get("username") if du else None,
            "avatar": du.get("avatar") if du else None,
        } if du else None,
        "guild_count": len(guilds),
        "has_plex": bool(PLEX_URL),
        "plex_owner": PLEX_OWNER or "",
        "plex_platform": PLEX_PLATFORM or "",
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
    keys = list(session.keys())
    return jsonify({
        "keys": keys,
        "has_discord_user": "discord_user" in session,
        "has_guilds": "guilds" in session,
        "permanent": getattr(session, "permanent", None),
        "cookie_name": app.config.get("SESSION_COOKIE_NAME"),
    })

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
    
@app.route('/favicon.ico')
@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
def serve_univfied_icon():
    return app.send_static_file('verseicon.png')
    
print("✅ Registered routes:")
for rule in app.url_map.iter_rules():
    print(" ", rule)

if __name__ == "__main__":
    print("🚀 MessiahBot Dashboard starting...")
    print(f"🌎 Environment: {ENVIRONMENT}")
    print(f"🧠 Flask templates: {app.template_folder}")
    print(f"🎨 Flask static: {app.static_folder}")
    print("🔐 Session configuration loaded successfully with cookie-based sessions.")
    app.run(host="0.0.0.0", port=PORT)
