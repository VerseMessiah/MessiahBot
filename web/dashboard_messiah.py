import os, time, json
from flask import Flask, jsonify, render_template, request
from flask_talisman import Talisman
from flask_session import Session
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("DASHBOARD_SESSION_SECRET", "local_dev_secret")

# Relax CSP for inline templates (you already opted to remove strict CSP earlier)
Talisman(app, force_https=True, content_security_policy=None)

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "/tmp/flask_sessions"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_USE_SIGNER"] = True
Session(app)

@app.after_request
def allow_inline(resp):
    resp.headers.pop("Content-Security-Policy", None)
    return resp

# --- Health ---
@app.get("/ping")
def ping():
    return "pong", 200, {"Content-Type": "text/plain"}

@app.get("/envcheck")
def envcheck():
    return {
        "environment": os.getenv("ENVIRONMENT", "PRD"),
        "has_plex": bool(os.getenv("PLEX_URL") and os.getenv("PLEX_TOKEN"))
    }

# --- Index & Form ---
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/form")
def form():
    return render_template("form.html")

# --- Plex: list libraries (dashboard view) ---
@app.get("/plex/libraries")
def plex_libraries():
    try:
        from bot.utils.plex_utils import get_plex_client
        plex = get_plex_client()
    except Exception as e:
        return {"ok": False, "error": f"Plex not configured: {e}"}, 500

    try:
        sections = plex.library.sections()
        libs = []
        for sec in sections:
            try:
                count = sec.totalSize
            except Exception:
                count = None
            libs.append({"title": sec.title, "type": getattr(sec, 'type', None), "count": count})
        return {"ok": True, "libraries": libs}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))
    app.run(host="0.0.0.0", port=port, debug=True)
