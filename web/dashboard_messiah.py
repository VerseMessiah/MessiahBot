import os
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
from bot.integrations.discord_oauth import discord_bp
from bot.integrations.twitch_bp import twitch_bp

load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "PRD").upper()
PORT = int(os.getenv("PORT", 5000))
REDIS_URL = os.getenv("REDIS_URL", None)
PLEX_URL = os.getenv("PLEX_URL", None)
PLEX_TOKEN = os.getenv("PLEX_TOKEN", None)
PLEX_OWNER = os.getenv("PLEX_OWNER", None)
PLEX_PLATFORM = os.getenv("PLEX_PLATFORM", None)

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

app.secret_key

app.register_blueprint(discord_bp)
app.register_blueprint(twitch_bp)

@app.route("/")
def index():
    return render_template("index.html", env=ENVIRONMENT)

@app.route("/form")
def form():
    return render_template("form.html", env=ENVIRONMENT)

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

@app.route("/envcheck")
def envcheck():
    return jsonify({
        "status": "ok",
        "plex": bool(PLEX_URL and PLEX_TOKEN),
        "twitch": bool(os.getenv("TWITCH_CLIENT_ID")),
        "discord": bool(os.getenv("DISCORD_APP_CLIENT_ID"))
    })

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

if __name__ == "__main__":
    print("🚀 MessiahBot Dashboard starting...")
    print(f"🌎 Environment: {ENVIRONMENT}")
    print(f"🧠 Flask templates: {app.template_folder}")
    print(f"🎨 Flask static: {app.static_folder}")
    app.run(host="0.0.0.0", port=PORT)
