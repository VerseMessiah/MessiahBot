import os
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv

# ----------------------------------------------------------
# Load environment (Render + local dev)
# ----------------------------------------------------------
load_dotenv()

ENVIRONMENT = os.getenv("ENVIRONMENT", "TST").upper()
PORT = int(os.getenv("PORT", 5000))
REDIS_URL = os.getenv("REDIS_URL", None)
PLEX_URL = os.getenv("PLEX_URL", None)
PLEX_TOKEN = os.getenv("PLEX_TOKEN", None)
PLEX_OWNER = os.getenv("PLEX_OWNER", None)
PLEX_PLATFORM = os.getenv("PLEX_PLATFORM", None)

# ----------------------------------------------------------
# Flask app setup
# ----------------------------------------------------------
app = Flask(
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
    static_folder=os.path.join(os.path.dirname(__file__), "static")
)

# ----------------------------------------------------------
# Routes
# ----------------------------------------------------------
@app.route("/")
def index():
    """Basic landing page."""
    return render_template("index.html", env=ENVIRONMENT)

@app.route("/form")
def form():
    """Main dashboard form."""
    return render_template("form.html", env=ENVIRONMENT)

@app.route("/ping")
def ping():
    """Health check endpoint for Render."""
    return jsonify({"ok": True, "env": ENVIRONMENT})

@app.route("/api/envcheck")
def envcheck():
    return jsonify({
        "status": "ok",
        "plex": bool(os.getenv("PLEX_URL")),
        "twitch": bool(os.getenv("TWITCH_CLIENT_ID"))
    })

@app.route("/whoami")
def whoami():
    """Diagnostic endpoint showing current environment & Plex config."""
    return jsonify({
        "environment": ENVIRONMENT,
        "plex_url": PLEX_URL,
        "plex_owner": PLEX_OWNER,
        "plex_platform": PLEX_PLATFORM,
        "redis_url": REDIS_URL,
    })

@app.route("/redis/status")
def redis_status():
    """Quick check to confirm Redis connection."""
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

# ----------------------------------------------------------
# Optional Plex endpoint (safe to leave in)
# ----------------------------------------------------------
@app.route("/plex/status")
def plex_status():
    """Simple check to see if Plex variables are configured."""
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
 
# ----------------------------------------------------------
# 🔍 Template visibility debug route (auto-disabled in PRD)
# ----------------------------------------------------------
@app.route("/debug/templates")
def debug_templates():
    """Lists what Flask can actually see in its templates folder."""
    from jinja2 import Environment, FileSystemLoader
    import os

    # Only allow access outside production
    if ENVIRONMENT in ("PRD", "PRODUCTION"):
        return {"error": "This endpoint is disabled in production."}, 403

    searchpath = app.jinja_loader.searchpath[0]
    abs_path = os.path.abspath(searchpath)
    files = []
    for root, _, f in os.walk(abs_path):
        for name in f:
            files.append(os.path.relpath(os.path.join(root, name), abs_path))

    return {
        "environment": ENVIRONMENT,
        "cwd": os.getcwd(),
        "template_folder": abs_path,
        "found_templates": files,
    }

# ----------------------------------------------------------
# 🎨 Static asset visibility debug route (auto-disabled in PRD)
# ----------------------------------------------------------
@app.route("/debug/static")
def debug_static():
    """Lists what Flask can actually see in its static folder."""
    import os

    # Disable in production for safety
    if ENVIRONMENT in ("PRD", "PRODUCTION"):
        return {"error": "This endpoint is disabled in production."}, 403

    abs_path = os.path.abspath(app.static_folder)
    files = []
    for root, _, f in os.walk(abs_path):
        for name in f:
            files.append(os.path.relpath(os.path.join(root, name), abs_path))

    return {
        "environment": ENVIRONMENT,
        "cwd": os.getcwd(),
        "static_folder": abs_path,
        "found_static_files": files,
    }


# ----------------------------------------------------------
# Main entrypoint
# ----------------------------------------------------------
if __name__ == "__main__":
    print("🚀 MessiahBot Dashboard starting...")
    print(f"🌎 Environment: {ENVIRONMENT}")
    print(f"🧠 Flask templates: {app.template_folder}")
    print(f"🎨 Flask static: {app.static_folder}")
    print(f"🎬 Plex URL: {PLEX_URL or 'not set'}")
    app.run(host="0.0.0.0", port=PORT)
