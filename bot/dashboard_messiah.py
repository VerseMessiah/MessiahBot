# bot/dashboard_messiah.py
import os
import json
import time
import secrets
import urllib.parse

from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

DISCORD_API = "https://discord.com/api/v10"
# OAuth endpoints (different base than REST!)
DISCORD_OAUTH_AUTHORIZE = "https://discord.com/oauth2/authorize"
DISCORD_OAUTH_TOKEN = "https://discord.com/api/oauth2/token"

def get_oauth_env():
    cid = (os.getenv("DISCORD_CLIENT_ID") or "").strip()
    secret = (os.getenv("DISCORD_CLIENT_SECRET") or "").strip()
    redirect = (os.getenv("DISCORD_REDIRECT_URI") or "").strip()
    return cid, secret, redirect

_psycopg_ok = False
try:
    import psycopg  # v3
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

# requests is needed for Discord REST + OAuth
_requests_ok = False
try:
    import requests
    _requests_ok = True
except Exception:
    _requests_ok = False

app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SESSION_SECRET", secrets.token_hex(32))
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
)

# ---------- DB helpers ----------
def _db_exec(q: str, p=()):
    if not (_psycopg_ok and DATABASE_URL):
        raise RuntimeError("DATABASE_URL not configured or psycopg not available")
    with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(q, p)

def _db_one(q: str, p=()):
    if not (_psycopg_ok and DATABASE_URL):
        return None
    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(q, p)
            return cur.fetchone()

def _json_equal(a, b) -> bool:
    try:
        return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    except Exception:
        return False

def _discord_headers():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set on web service")
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "User-Agent": "MessiahBotDashboard/1.0",
        "Content-Type": "application/json",
    }

# ---------- robust GET with retry/backoff (helps with 1015/429/5xx) ----------
def _get_with_retry(url, headers, tries=3, backoff=0.6):
    if not _requests_ok:
        raise RuntimeError("requests not available")
    last = None
    for i in range(tries):
        r = requests.get(url, headers=headers, timeout=20)
        # Rate limited
        if r.status_code == 429 and i < tries - 1:
            try:
                delay = float(r.headers.get("Retry-After") or backoff * (i + 1))
            except Exception:
                delay = backoff * (i + 1)
            time.sleep(delay)
            continue
        # Upstream 5xx
        if r.status_code >= 500 and i < tries - 1:
            time.sleep(backoff * (i + 1))
            continue
        return r
    return r

# ---------- OAuth helpers ----------
def _discord_oauth_url(state: str):
    cid, _, redirect = get_oauth_env()
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": "identify guilds",
        "state": state,
        "prompt": "consent",
    }
    return f"{DISCORD_OAUTH_AUTHORIZE}?{urllib.parse.urlencode(params)}"

def _exchange_code_for_token(code: str):
    if not _requests_ok:
        raise RuntimeError("Python 'requests' not installed; add requests to requirements.txt")
    cid, secret, redirect = get_oauth_env()
    data = {
        "client_id": cid,
        "client_secret": secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(DISCORD_OAUTH_TOKEN, data=data, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()

def _discord_get(me_endpoint: str, access_token: str):
    if not _requests_ok:
        raise RuntimeError("Python 'requests' not installed; add requests to requirements.txt")
    r = requests.get(
        f"{DISCORD_API}{me_endpoint}",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()

# ---------- probes ----------
@app.get("/ping")
def ping():
    return "pong", 200, {"Content-Type": "text/plain"}

@app.get("/routes")
def routes():
    return {"routes": sorted([str(r.rule) for r in app.url_map.iter_rules()])}

@app.get("/dbcheck")
def dbcheck():
    ok_env = bool(DATABASE_URL)
    try:
        import psycopg  # noqa
        ok_driver = True
        driver_version = psycopg.__version__
    except Exception:
        ok_driver = False
        driver_version = None

    ok_connect = False
    if ok_env and ok_driver:
        try:
            with psycopg.connect(DATABASE_URL, sslmode="require"):
                ok_connect = True
        except Exception:
            ok_connect = False

    cid, secret, redirect = get_oauth_env()
    status = {
      "database_url_present": ok_env,
      "psycopg_available": ok_driver,
      "psycopg_version": driver_version,
      "can_connect": ok_connect,
      "has_discord_bot_token": bool(DISCORD_BOT_TOKEN),
      "has_requests": _requests_ok,
      "has_oauth": bool(cid and secret and redirect),
    }
    code = 200 if (ok_env and ok_driver and ok_connect) else 500
    return status, code

@app.get("/envcheck")
def envcheck():
    cid, secret, redirect = get_oauth_env()
    return {
        "client_id": cid,
        "client_secret_len": len(secret),
        "redirect_uri": redirect,
        "has_bot_token": bool(DISCORD_BOT_TOKEN),
    }

@app.get("/oauth/debug")
def oauth_debug():
    cid, secret, redirect = get_oauth_env()
    try:
        debug_url = _discord_oauth_url("debug-state")
    except Exception as e:
        debug_url = f"<error building url: {e}>"
    return {
        "client_id": cid,
        "has_client_secret": bool(secret),
        "redirect_uri": redirect,
        "authorize_url": debug_url,
    }

@app.get("/oauth/authorize_url")
def oauth_authorize_url():
    try:
        url = _discord_oauth_url("inspect-only")
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500
    return {"ok": True, "authorize_url": url}

# ---------- OAuth routes ----------
@app.get("/login")
def discord_login():
    try:
        cid, secret, redirect_uri = get_oauth_env()
        print("[OAuth] /login env",
              {"client_id": cid, "has_secret": bool(secret), "redirect_uri": redirect_uri})
        if not (cid and secret and redirect_uri):
            return ("Discord OAuth not configured (need DISCORD_CLIENT_ID, "
                    "DISCORD_CLIENT_SECRET, DISCORD_REDIRECT_URI)"), 500

        state = secrets.token_urlsafe(32)
        session["oauth_state"] = state

        auth_url = _discord_oauth_url(state)
        print("[OAuth] redirecting to", auth_url)
        return redirect(auth_url)
    except Exception as e:
        import traceback
        print("[OAuth] /login error:", e)
        traceback.print_exc()
        return f"/login failed: {e}", 500

@app.get("/oauth/discord/callback")
def discord_callback():
    err = request.args.get("error")
    if err:
        return f"OAuth error: {err}", 400
    state = request.args.get("state")
    code = request.args.get("code")
    if not state or not code or state != session.get("oauth_state"):
        return "Invalid OAuth state", 400
    session.pop("oauth_state", None)

    try:
        tok = _exchange_code_for_token(code)
    except Exception as e:
        return f"Token exchange failed: {e}", 400

    access = tok.get("access_token")
    refresh = tok.get("refresh_token")
    expires_in = tok.get("expires_in")  # seconds
    if not access:
        return "Token exchange succeeded but no access_token in response.", 400

    session.clear()
    session["access_token"] = access
    if refresh:
        session["refresh_token"] = refresh
    if expires_in:
        session["token_expiry"] = int(time.time()) + int(expires_in)

    try:
        me = _discord_get("/users/@me", access)
        print(f"[OAuth] login ok: user={me.get('id')} {me.get('username')}#{me.get('discriminator')}")
    except Exception as e:
        print(f"[OAuth] token test failed: {e}")

    return redirect(url_for("form"))


# Helper: refresh Discord OAuth access token using refresh_token
def _refresh_access_token():
    if not _requests_ok:
        raise RuntimeError("Python 'requests' not installed; add requests to requirements.txt")
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("No refresh_token in session")
    cid, secret, redirect = get_oauth_env()
    data = {
        "client_id": cid,
        "client_secret": secret,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "redirect_uri": redirect,
        "scope": "identify guilds",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    r = requests.post(DISCORD_OAUTH_TOKEN, data=data, headers=headers, timeout=20)
    r.raise_for_status()
    tok = r.json()
    access = tok.get("access_token")
    refresh = tok.get("refresh_token")
    expires_in = tok.get("expires_in")
    if not access:
        raise RuntimeError("Failed to refresh token: no access_token in response")
    session["access_token"] = access
    if refresh:
        session["refresh_token"] = refresh
    if expires_in:
        session["token_expiry"] = int(time.time()) + int(expires_in)
    return access

@app.get("/logout")
def discord_logout():
    session.clear()
    return redirect(url_for("form"))

@app.get("/whoami")
def whoami():
    access = session.get("access_token")
    refresh = session.get("refresh_token")
    expiry = session.get("token_expiry")
    now = int(time.time())
    # If we have expiry and it's expired or about to expire in 60s, refresh
    if access and expiry and expiry - now < 60 and refresh:
        try:
            access = _refresh_access_token()
        except Exception as e:
            import traceback; traceback.print_exc()
            # Clear session on refresh failure
            session.clear()
            return {
                "logged_in": False,
                "me": None,
                "guilds": [],
                "error": f"Token refresh failed: {e}",
                "has_session_cookie": bool(request.cookies.get("session")),
            }
    if not access:
        return {
            "logged_in": False,
            "me": None,
            "guilds": [],
            "has_session_cookie": bool(request.cookies.get("session")),
        }
    try:
        me = _discord_get("/users/@me", access)
        guilds = _discord_get("/users/@me/guilds", access)
        return {
            "logged_in": True,
            "me": me,
            "guilds": guilds,
            "has_session_cookie": bool(request.cookies.get("session")),
        }
    except Exception as e:
        import traceback; traceback.print_exc()
        return {
            "logged_in": False,
            "me": None,
            "guilds": [],
            "error": str(e),
            "has_session_cookie": bool(request.cookies.get("session")),
        }

@app.get("/sessiondump")
def sessiondump():
    return {
        "keys": list(session.keys()),
        "has_session_cookie": bool(request.cookies.get("session")),
    }

# ---------- live snapshot via Discord REST ----------
@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    Build a layout from the current live Discord server using REST.
    Adds `original_type` to channels so the UI can lock/limit conversions.
    """
    if not _requests_ok:
        return jsonify({"ok": False, "error": "Python 'requests' not installed; add requests to requirements.txt"}), 500

    if not DISCORD_BOT_TOKEN:
        return jsonify({"ok": False, "error": "DISCORD_BOT_TOKEN not set on web service"}), 500

    base = "https://discord.com/api/v10"
    headers = _discord_headers()

    # roles
    r_roles = _get_with_retry(f"{base}/guilds/{guild_id}/roles", headers)
    if r_roles.status_code == 403:
        return jsonify({"ok": False, "error": "Forbidden: bot lacks permission or is not in this guild"}), 403
    if r_roles.status_code == 404:
        return jsonify({"ok": False, "error": "Guild not found (check Guild ID)"}), 404
    if r_roles.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord roles error {r_roles.status_code}: {r_roles.text}"}), 502
    roles_json = r_roles.json()

    # channels
    r_channels = _get_with_retry(f"{base}/guilds/{guild_id}/channels", headers)
    if r_channels.status_code == 403:
        return jsonify({"ok": False, "error": "Forbidden fetching channels (permissions?)"}), 403
    if r_channels.status_code == 404:
        return jsonify({"ok": False, "error": "Guild channels not found"}), 404
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord channels error {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    # Convert roles (skip @everyone and managed) and sort by position DESC (Discord: higher = higher)
    roles = []
    for r in sorted(roles_json, key=lambda x: x.get("position", 0), reverse=True):
        if r.get("managed"):
            continue
        if r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # Categories: build ordered objects with position
    cats = {}
    for c in channels_json:
        if c.get("type") == 4:
            cats[c["id"]] = {
                "id": c["id"],
                "name": c.get("name", "") or "",
                "position": c.get("position", 0),
                "channels": []
            }
    # Ensure we have an "uncategorized" bucket for channels without parent
    UNC_KEY = "__uncat__"
    if UNC_KEY not in cats:
        cats[UNC_KEY] = {"id": "", "name": "", "position": 10_000_000, "channels": []}

    # helper to map Discord API types -> our UI types
    def ui_type(api_type: int) -> str:
        if api_type == 0:   # text
            return "text"
        if api_type == 2:   # voice
            return "voice"
        if api_type == 4:   # category
            return "category"
        if api_type == 5:   # news/announcement
            return "announcement"
        if api_type == 15:  # forum
            return "forum"
        if api_type == 13:  # stage
            return "stage"
        return "text"

    # Build mapping of channels by parent category for proper ordering
    channels_by_cat = {cat_id: [] for cat_id in cats}
    # Also, keep the uncategorized bucket
    channels_by_cat[UNC_KEY] = channels_by_cat.get(UNC_KEY, [])
    # Assign non-category channels to their parent category (or uncategorized)
    for ch in channels_json:
        if ch.get("type") == 4:
            continue
        parent_id = ch.get("parent_id")
        bucket_key = parent_id if parent_id in cats else UNC_KEY
        t_ui = ui_type(ch.get("type"))
        # Include position and id for sorting
        channel_obj = {
            "name": ch.get("name", ""),
            "type": t_ui,              # desired type (UI may change)
            "original_type": t_ui,     # live type (for validation)
            "topic": ch.get("topic", "") or "",
            "position": ch.get("position", 0),
            "id": ch.get("id", "")
        }
        channels_by_cat[bucket_key].append(channel_obj)
    # Now, for each category, sort its channels by position, then id as tiebreaker
    for cat_id, cat in cats.items():
        chs = channels_by_cat.get(cat_id, [])
        cat["channels"] = sorted(
            chs,
            key=lambda x: (x.get("position", 0), str(x.get("id", "")))
        )

    # Build ordered categories list for payload (ascending by position; uncategorized at end)
    categories_ordered = sorted(cats.values(), key=lambda c: c["position"])
    # Include position for categories and for channels
    categories_payload = [
        {
            "name": c["name"],
            "position": c["position"],
            "channels": [
                {
                    "name": ch["name"],
                    "type": ch["type"],
                    "original_type": ch["original_type"],
                    "topic": ch["topic"],
                    "position": ch["position"],
                    "id": ch["id"]
                }
                for ch in c["channels"]
            ]
        }
        for c in categories_ordered
    ]

    payload = {
        "mode": "update",
        "roles": roles,
        "categories": categories_payload
    }
    return jsonify({"ok": True, "payload": payload})

# ---------- layout storage API ----------
@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Validates channel type changes according to original_type (if present).
    """
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    incoming = request.get_json(silent=True) or {}
    if not incoming:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    # Validate channel type conversions
    warnings = []
    channels = incoming.get("channels") or []

    def compatible(live_type: str, req_type: str) -> bool:
        if live_type in ("text", "announcement"):
            return req_type in ("text", "announcement")
        if live_type in ("voice", "stage"):
            return req_type in ("voice", "stage")
        if live_type == "forum":
            return req_type == "forum"
        return True

    for ch in channels:
        live = (ch.get("original_type") or "").lower().strip()
        req = (ch.get("type") or "text").lower().strip()
        if live and not compatible(live, req):
            ch["type"] = live
            warnings.append(
                f"Channel '{ch.get('name','')}' type reset to '{live}' (conversion from '{req}' not supported)."
            )

    latest = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if latest and _json_equal(incoming, latest["payload"]):
        return jsonify({"ok": True, "version": int(latest["version"]), "no_change": True, "warnings": warnings})

    row = _db_one(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM builder_layouts WHERE guild_id = %s",
        (guild_id,),
    )
    version = int((row or {}).get("v", 1))

    _db_exec(
        "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s, %s, %s::jsonb)",
        (guild_id, version, json.dumps(incoming)),
    )
    return jsonify({"ok": True, "version": version, "no_change": False, "warnings": warnings})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id: str):
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No layout"}), 404
    return jsonify({"ok": True, "version": int(row["version"]), "payload": row["payload"]})

# ---------- Form UI ----------
_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Server Builder</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { color-scheme: dark light; }
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,"Helvetica Neue",Arial;
         max-width:1000px;margin:24px auto;padding:0 12px;background:#0b0b0f;color:#e7e7ea}
    a{color:#8ab4ff}
    fieldset{margin:16px 0;padding:12px;border-radius:10px;border:1px solid #2a2a34}
    input,select,button{font:inherit}
    input[type="text"]{background:#11131a;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:6px 8px}
    select{background:#11131a;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:6px 8px}
    button{background:#1a1f2b;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:8px 10px;cursor:pointer}
    .row{display:flex;gap:6px;align-items:center;margin:6px 0}
    .stack{display:flex;flex-direction:column;gap:6px}
    .subtle{opacity:.8}
    .pill{display:inline-flex;gap:6px;align-items:center;padding:2px 8px;border:1px solid #2a2a34;border-radius:999px;background:#131722;font-size:12px}
    .grid{display:grid;grid-template-columns: 1fr 1fr;gap:12px}
    .list{padding:8px;border:1px dashed #2a2a34;border-radius:10px;background:#0f1219}
    .cat{padding:8px;border:1px solid #2a2a34;border-radius:10px;background:#0f1219;margin:8px 0}
    .ch{display:flex;gap:6px;align-items:center;margin:6px 0}
    .muted{opacity:.6}
    .bar{display:flex;align-items:center;justify-content:space-between;margin-bottom:12px}
    .right{display:flex;gap:8px;align-items:center}
    .select{background:#11131a;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:6px 8px}
    .draggable {cursor: grab;}
    .drag-ghost {opacity: 0.5; background: #22242a;}
    .drag-over {border: 2px dashed #8ab4ff; outline-offset: 2px; background: #1a1f2b;}

    /* Added: clearer handles and dropzone styling */
    .grab{ -webkit-user-select:none; user-select:none; touch-action:none; }
    .grab{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;background:#1a1f2b;border:1px solid #2a2a34;margin-right:6px;cursor:grab;user-select:none}
    .grab:active{cursor:grabbing}
    .row,.ch,.cat>.row{position:relative}
    .drag-ghost{opacity:.55;transform:scale(.98)}
    .drag-over{border:2px dashed #8ab4ff !important;background:#131722 !important}
    .dropzone{transition:background .12s ease,border .12s ease}
    /* Make empty channel lists easy to drop into */
    .ch-list{min-height:24px;padding:6px;border-radius:8px}
    .ch-list:empty::after{content:"(drop channels here)";opacity:.45;font-size:12px}
  
    /* scope to just the DnD area */
    .dnd-scope .drag-handle {
      width: 14px; height: 14px; border-radius: 4px;
      background: linear-gradient(180deg, #7a86ff, #5460ff);
      cursor: grab;
      display:inline-block;
      margin-right: 8px;
      flex: 0 0 14px;
    }
    .dnd-scope .drag-handle:active { cursor: grabbing; }

    .dnd-scope .dnd-item {
      display: flex; align-items: center; gap: 10px;
      padding: 8px 10px; margin: 6px 0;
      border: 1px solid rgba(124,130,255,0.25);
      border-radius: 10px; background: rgba(255,255,255,0.03);
    }
    .dnd-scope .ghost { opacity: 0.35; }
    .dnd-scope .is-dragging { opacity: 0.9; transform: scale(1.01); }

    /* Role permissions panel */
    .perm-panel { display:none; padding:8px; margin-left:28px; border:1px dashed #2a2a34; border-radius:8px; background:#11131a }
    .perm-panel .col { display:flex; flex-direction:column; gap:6px }
    .perm-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap }
    .perm-row label { display:flex; align-items:center; gap:6px; font-size:13px; opacity:.9 }
    .perm-toggle { margin-left:8px }

    /* Only prevent selection on draggable items (not inputs/buttons elsewhere) */
    .dnd-scope .dnd-item, 
    .dnd-scope .drag-handle {
      -webkit-user-select: none; user-select: none; -webkit-user-drag: none;
    }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>
</head>
<body>
  <div class="bar">
    <div>
      <strong>🧱 MessiahBot — Server Builder</strong>
      <span id="who" class="pill muted" style="margin-left:8px;">not signed in</span>
    </div>
    <div class="right">
      <select id="guildPicker" class="select" style="display:none;"></select>
      <a id="inviteBtn" class="pill" href="#" target="_blank" rel="noopener">Invite Bot</a>
      <a id="loginBtn" class="pill" href="/login">Login with Discord</a>
      <a id="logoutBtn" class="pill" href="/logout" style="display:none;">Logout</a>
    </div>
  </div>

  <p class="subtle">Enter your Guild ID, then <strong>Load From Live</strong> or <strong>Load Snapshot</strong>, edit, and <strong>Save</strong>.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a> •
    <a href="/whoami" target="_blank">/whoami</a>
  </p>

  <form id="layoutForm" class="stack" name="layoutForm">
    <div class="row">
      <label>Guild ID <input type="text" id="guild_id" required placeholder="123456789012345678"></label>
      <button type="button" id="loadLatestBtn">Load Snapshot</button>
      <button type="button" id="loadLiveBtn">Load From Live</button>
      <span id="status" class="pill">idle</span>
    </div>

    <fieldset>
      <legend>Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <section>
      <h3>Roles</h3>
      <div id="roles" class="list"></div>
      <button type="button" id="addRoleBtn">Add Role</button>
    </section>

    <section>
      <h3>Categories & Channels</h3>
      <div id="cats" class="list"></div>
      <button type="button" id="addCatBtn">Add Category</button>
    </section>

    <section>
      <h3>Danger Zone</h3>
      <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
      <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
      <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>
    </section>

    <div class="row">
      <button type="button" id="saveBtn">Save Layout</button>
      <span id="saveNote" class="pill muted"></span>
    </div>
  </form>

  <script>
  // ---------- utilities ----------
  function $(sel, el){ return (el||document).querySelector(sel); }
  function $all(sel, el){ return Array.prototype.slice.call((el||document).querySelectorAll(sel)); }
  var statusPill = $("#status");
  function setStatus(txt){ statusPill.textContent = txt; }

  // ---------- Drag & Drop (SortableJS) ----------
  // We use SortableJS for reliability across Safari/Chrome/Firefox.
  // Handles are the ".grab" elements already present in the markup.

  function initRolesSortable() {
    var el = document.getElementById('roles');
    if (!el || el._sortableInit) return;
    el._sortableInit = true;
    new Sortable(el, {
      handle: '.grab',
      draggable: '.row',
      animation: 150,
      chosenClass: 'is-dragging',
      fallbackTolerance: 5,
      ghostClass: 'drag-ghost',
      dragClass: 'is-dragging'
    });
  }

  function initCategoriesSortable() {
    var wrap = document.getElementById('cats');
    if (!wrap || wrap._sortableInit) return;
    wrap._sortableInit = true;
    new Sortable(wrap, {
      handle: '.cat > .row .grab',
      draggable: '.cat',
      animation: 150,
      chosenClass: 'is-dragging',
      fallbackTolerance: 5,
      ghostClass: 'drag-ghost',
      dragClass: 'is-dragging'
    });
  }

  function initChannelListSortable(listEl) {
    if (!listEl || listEl._sortableInit) return;
    listEl._sortableInit = true;
    new Sortable(listEl, {
      group: { name: 'channels', pull: true, put: true },
      handle: '.ch .grab',
      draggable: '.ch',
      animation: 150,
      chosenClass: 'is-dragging',
      fallbackTolerance: 5,
      bubbleScroll: true,
      scroll: true,
      ghostClass: 'drag-ghost',
      dragClass: 'is-dragging'
    });
  }

  function initChannelLists() {
    document.querySelectorAll('#cats .ch-list').forEach(initChannelListSortable);
  }

  function initSortables() {
    initRolesSortable();
    initCategoriesSortable();
    initChannelLists();
  }
  function addRoleRow(name, color, perms){
    if (!name) name = "";
    if (!color) color = "#000000";
    perms = perms || null;

    var d = document.createElement('div');
    d.className = "row";
    d.setAttribute("draggable", "true");
    d.innerHTML =
      '<span class="grab" title="Drag">⋮⋮</span>'+
      '<input placeholder="Role" name="role_name" value="'+name+'">'+
      '<input type="color" name="role_color" value="'+color+'">'+
      '<label class="perm-toggle subtle"><input type="checkbox" class="role-apply-perms"> Set/Update permissions</label>'+
      '<button type="button" class="del">✕</button>'+
      '<div class="perm-panel">'+
        '<div class="perm-row">'+
          '<label><input type="checkbox" class="perm-admin"> Admin</label>'+
          '<label><input type="checkbox" class="perm-manage-channels"> Manage Channels</label>'+
          '<label><input type="checkbox" class="perm-manage-roles"> Manage Roles</label>'+
          '<label><input type="checkbox" class="perm-view"> View Channel</label>'+
          '<label><input type="checkbox" class="perm-send"> Send Messages</label>'+
          '<label><input type="checkbox" class="perm-connect"> Connect (Voice)</label>'+
          '<label><input type="checkbox" class="perm-speak"> Speak</label>'+
        '</div>'+
      '</div>';

    // delete handler
    d.querySelector(".del").onclick = function(){ d.remove(); };

    // toggle panel
    var toggle = d.querySelector(".role-apply-perms");
    var panel = d.querySelector(".perm-panel");
    function setPanel(v){ panel.style.display = v ? 'block' : 'none'; }
    toggle.addEventListener('change', function(){ setPanel(toggle.checked); });

    // prefill from perms (if provided)
    if (perms && typeof perms === 'object') {
      toggle.checked = true;
      setPanel(true);
      if (perms.admin) d.querySelector('.perm-admin').checked = true;
      if (perms.manage_channels) d.querySelector('.perm-manage-channels').checked = true;
      if (perms.manage_roles) d.querySelector('.perm-manage-roles').checked = true;
      if (perms.view_channel) d.querySelector('.perm-view').checked = true;
      if (perms.send_messages) d.querySelector('.perm-send').checked = true;
      if (perms.connect) d.querySelector('.perm-connect').checked = true;
      if (perms.speak) d.querySelector('.perm-speak').checked = true;
    } else {
      setPanel(false);
    }

    document.getElementById('roles').appendChild(d);
  }

  // ---------- Categories/Channels UI ----------
  function catBox(name){
    if (!name) name = "";
    var wrap = document.createElement('div');
    wrap.className = "cat";
    wrap.innerHTML =
      '<div class="row">'+
        '<span class="grab" title="Drag">⋮⋮</span>'+
        '<strong>Category</strong>'+
        '<input placeholder="Category name (blank = uncategorized bucket)" class="cat-name" value="'+name+'">'+
        '<button type="button" class="addChan">+ Channel</button>'+
        '<button type="button" class="delCat">✕</button>'+
      '</div>'+
      '<div class="stack ch-list"></div>';
    wrap.querySelector(".delCat").onclick = function(){ wrap.remove(); };

    wrap.querySelector(".addChan").onclick = function(){
      var row = channelRow({});
      $(".ch-list", wrap).appendChild(row);
    };

    // ensure the new channel list is sortable
    initChannelListSortable($(".ch-list", wrap));
    return wrap;
  }

  function channelRow(ch){
    ch = ch || {};
    var name = ch.name || "";
    var type = (ch.type || "text").toLowerCase();
    var original = ch.original_type ? ch.original_type.toLowerCase() : null;
    var topic = ch.topic || (ch.options && ch.options.topic) || "";

    var full = ["text","announcement","voice","stage","forum"];
    var opts = full.slice(0);
    var lockedLabel = "";
    if (original){
      if (["text","announcement"].indexOf(original) >= 0){ opts = ["text","announcement"]; }
      else if (["voice","stage"].indexOf(original) >= 0){ opts = ["voice","stage"]; }
      else if (original === "forum"){ opts = ["forum"]; lockedLabel = "Forum · locked"; }
    }
    var useType = (opts.indexOf(type) >= 0) ? type : (original || "text");

    var d = document.createElement('div');
    d.className = "ch";
    d.setAttribute("draggable", "true");

    var selectHTML;
    if (opts.length === 1 && opts[0] === "forum"){
      selectHTML =
        '<span class="pill">'+(lockedLabel || 'Forum · locked')+'</span>'+
        '<input type="hidden" class="ch-type" value="forum">';
    } else {
      var options = '';
      for (var i=0;i<opts.length;i++){
        var o = opts[i];
        options += '<option value="'+o+'"'+(o===useType?' selected':'')+'>'+o+'</option>';
      }
      selectHTML = '<select class="ch-type">'+options+'</select>';
    }

    d.innerHTML =
      '<span class="grab" title="Drag">⋮⋮</span>'+
      '<input class="ch-name" placeholder="Channel name" value="'+name+'">'+
      selectHTML +
      '<input class="ch-topic" placeholder="Topic / Description" value="'+topic+'">'+
      '<button type="button" class="del">✕</button>';
    d.setAttribute("data-original-type", original || "");
    d.querySelector(".del").onclick = function(){ d.remove(); };
    return d;
  }

  // ---------- hydrate / collect ----------
  function hydrate(p){
    // Mode
    var mode = (p.mode || 'build');
    var radio = document.querySelector('input[name="mode"][value="' + mode + '"]');
    if (radio) radio.checked = true;

    // roles
    var R = $("#roles"); R.innerHTML = "";
    var roles = p.roles || [];
    for (var i=0;i<roles.length;i++){
      addRoleRow(roles[i].name || "", roles[i].color || "#000000", roles[i].perms || null);
    }
    if (roles.length === 0) addRoleRow("", "#000000");

    // categories + channels
    var C = $("#cats"); C.innerHTML = "";
    if (Array.isArray(p.categories) && p.categories.length && typeof p.categories[0] === "object"){
      for (var ci=0;ci<p.categories.length;ci++){
        var cat = p.categories[ci] || {};
        var box = catBox(cat.name || "");
        var listEl = $(".ch-list", box);
        var chans = cat.channels || []).slice();
        // sort by position then name as fallback
        chans.sort(function(a,b){
          if (a.position !== undefined && b.position !== undefined && a.position !== b.position){
            return a.position - b.position;
          }
          return (a.name || "").localeCompare(b.name || "");
        });
        for (var cj=0;cj<chans.length;cj++){
          listEl.appendChild(channelRow(chans[cj] || {}));
        }
        C.appendChild(box);
      }
    } else {
      var catNames = p.categories || [];
      var map = {};
      for (var k=0;k<catNames.length;k++){
        var nm = catNames[k] || "";
        var bx = catBox(nm);
        C.appendChild(bx);
        map[(nm||"").toLowerCase()] = $(".ch-list", bx);
      }
      var hadUn = false;
      var chansFlat = p.channels || [];
      for (var m=0;m<chansFlat.length;m++){
        var ch = chansFlat[m] || {};
        var parent = (ch.category || "").toLowerCase();
        var row = channelRow(ch);
        if (map[parent]) map[parent].appendChild(row);
        else {
          if (!hadUn){
            var u = catBox("");
            C.appendChild(u);
            map[""] = $(".ch-list", u);
            hadUn = true;
          }
          map[""].appendChild(row);
        }
      }
      if (C.children.length === 0){
        C.appendChild(catBox(""));
      }
    }

    // initialize SortableJS on all lists
    initSortables();

    // danger zone
    $("#prune_roles").checked = !!(p.prune && p.prune.roles);
    $("#prune_categories").checked = !!(p.prune && p.prune.categories);
    $("#prune_channels").checked = !!(p.prune && p.prune.channels);
  }

  function collectPayload(){
    var mode = document.forms.layoutForm.mode.value;

    // roles
    var roles = [];
    $all('#roles .row').forEach(function(r, idx){
      var name = r.querySelector('input[name="role_name"]').value.trim();
      var color = r.querySelector('input[name="role_color"]').value || "#000000";
      if (!name) return;

      var apply = r.querySelector('.role-apply-perms') && r.querySelector('.role-apply-perms').checked;
      var perms = {};
      if (apply) {
        if (r.querySelector('.perm-admin')?.checked) perms.admin = true;
        if (r.querySelector('.perm-manage-channels')?.checked) perms.manage_channels = true;
        if (r.querySelector('.perm-manage-roles')?.checked) perms.manage_roles = true;
        if (r.querySelector('.perm-view')?.checked) perms.view_channel = true;
        if (r.querySelector('.perm-send')?.checked) perms.send_messages = true;
        if (r.querySelector('.perm-connect')?.checked) perms.connect = true;
        if (r.querySelector('.perm-speak')?.checked) perms.speak = true;
      }

      var roleObj = {name:name, color:color, position: idx};
      if (apply && Object.keys(perms).length > 0) {
        roleObj.perms = perms;
      }
      roles.push(roleObj);
    });

    // categories (nested)
    var categories = [];
    $all('#cats .cat').forEach(function(catEl, cIdx){
      var cname = $('.cat-name', catEl).value.trim();
      var channels = [];
      $all('.ch-list .ch', catEl).forEach(function(chEl, chIdx){
        var nm = $('.ch-name', chEl).value.trim();
        if (!nm) return;
        var typeSel = $('.ch-type', chEl);
        var typ = typeSel ? typeSel.value : "forum";
        var topic = ($('.ch-topic', chEl) && $('.ch-topic', chEl).value) || "";
        var original_type = chEl.getAttribute('data-original-type') || null;
        channels.push({name:nm, type:typ, original_type:original_type, topic:topic, position: chIdx});
      });
      categories.push({name:cname, position: cIdx, channels:channels});
    });

    var prune = {
      roles: $("#prune_roles").checked,
      categories: $("#prune_categories").checked,
      channels: $("#prune_channels").checked
    };

    return { mode:mode, roles:roles, categories:categories, prune:prune };
  }

  // ---------- header login/guild picker ----------
  (async function initHeader(){
    try{
      const r = await fetch("/whoami");
      const info = await r.json();
      const who = $("#who");
      const loginBtn = $("#loginBtn");
      const logoutBtn = $("#logoutBtn");
      const picker = $("#guildPicker");
      const inviteBtn = $("#inviteBtn");

      // Fill invite link from env
      try {
        const envr = await fetch("/envcheck");
        const env = await envr.json();
        if (env.client_id) {
          const cid = encodeURIComponent(env.client_id);
          inviteBtn.href = "https://discord.com/oauth2/authorize?client_id=" + cid + "&scope=bot+applications.commands&permissions=8&integration_type=0";
        } else {
          inviteBtn.href = "#";
        }
      } catch(_e) {
        inviteBtn.href = "#";
      }

      if (info.logged_in && info.me){
        who.textContent = info.me.username + "#" + info.me.discriminator;
        who.classList.remove("muted");
        loginBtn.style.display = "none";
        logoutBtn.style.display = "inline-flex";

        const ADMINISTRATOR = 0x00000008;
        const MANAGE_GUILD  = 0x00000020;

        // Filter guilds: owner OR has ADMINISTRATOR/MANAGE_GUILD
        const guilds = (info.guilds || []).filter(g => {
          if (g.owner) return true;
          try {
            // Use BigInt to avoid precision loss on large permission values
            const p = BigInt(g.permissions):
            return (p & (BigInt(ADMINISTRATOR) | BigInt(MANAGE_GUILD))) !== 0n;
          } catch {
            return false;
          }
        });

        if (guilds.length){
          picker.innerHTML = '';
          guilds.forEach(g => {
            const opt = document.createElement('option');
            opt.value = g.id;
            opt.textContent = g.name || g.id;
            picker.appendChild(opt);
          });
          picker.style.display = "inline-flex";
          picker.onchange = function(){
            $("#guild_id").value = picker.value;
          };
          // prefill the first
          $("#guild_id").value = picker.value;
        } else {
          picker.style.display = "none";
        }
      } else {
        who.textContent = "not signed in";
        who.classList.add("muted");
        loginBtn.style.display = "inline-flex";
        logoutBtn.style.display = "none";
        picker.style.display = "none";
      }
    }catch(e){
      console.warn("whoami failed", e);
    }
  })();

  // ---------- buttons ----------
  $("#addRoleBtn").onclick = function(){ addRoleRow("", "#000000"); };
  $("#addCatBtn").onclick = function(){ $("#cats").appendChild(catBox("")); };

  $("#loadLiveBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    setStatus("loading live…");
    try{
      var res = await fetch("/api/live_layout/" + encodeURIComponent(gid));
      var data = await res.json();
      if (!data.ok){ alert(data.error || "Failed to load live"); setStatus("idle"); return; }
      hydrate(data.payload || {});
      setStatus("live loaded");
    }catch(e){
      setStatus("idle");
      alert("Failed to load live");
    }
  };

  $("#loadLatestBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    setStatus("loading snapshot…");
    try{
      var res = await fetch("/api/layout/" + encodeURIComponent(gid) + "/latest");
      var data = await res.json();
      if (!data.ok){ alert(data.error || "No layout"); setStatus("idle"); return; }
      hydrate(data.payload || {});
      setStatus("snapshot v" + data.version + " loaded");
    }catch(e){
      setStatus("idle");
      alert("Failed to load snapshot");
    }
  };

  $("#saveBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    var payload = collectPayload();

    // flatten channels for validation + legacy readers
    var flatChannels = [];
    for (var i=0;i<payload.categories.length;i++){
      var c = payload.categories[i];
      var cname = c.name || "";
      var chs = c.channels || [];
      for (var j=0;j<chs.length;j++){
        var ch = chs[j];
        flatChannels.push({
          name: ch.name,
          type: ch.type,
          original_type: ch.original_type || null,
          category: cname,
          category_position: i,
          position: j,
          options: { topic: ch.topic || "" }
        });
      }
    }

    var saveBody = {
      mode: payload.mode,
      roles: payload.roles,
      categories: payload.categories,
      channels: flatChannels,
      prune: payload.prune
    };

    setStatus("saving…");
    try{
      var res = await fetch("/api/layout/" + encodeURIComponent(gid), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(saveBody)
      });
      var data = await res.json();
      setStatus("saved");
      if (data.warnings && data.warnings.length){
        alert("Saved with warnings:\n\n" + data.warnings.join("\n"));
      } else if (data.ok && data.no_change){
        alert("No changes detected. Current version is still " + data.version + ".");
      } else if (data.ok){
        alert("Saved version " + data.version);
      } else {
        alert(data.error || "Error");
      }
    }catch(e){
      setStatus("idle");
      alert("Failed to save");
    }
  };

  // ---------- initial blank rows + DnD ----------
  addRoleRow("", "#000000");
  $("#cats").appendChild(catBox(""));
  initSortables();
  </script>
</body>
</html>
"""

@app.get("/")
def index():
    return (
        '<h1>MessiahBot Dashboard</h1>'
        '<p>Go to <a href="/form">/form</a> to submit or load a layout.</p>',
        200,
        {"Content-Type": "text/html"},
    )

@app.get("/form")
def form():
    return render_template_string(_FORM_HTML)

if __name__ == "__main__":
    # Local dev runner. On Render, prefer:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))