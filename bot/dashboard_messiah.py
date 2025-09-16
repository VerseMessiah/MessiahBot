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
    if not access:
        return "Token exchange succeeded but no access_token in response.", 400

    session.clear()
    session["access_token"] = access

    try:
        me = _discord_get("/users/@me", access)
        print(f"[OAuth] login ok: user={me.get('id')} {me.get('username')}#{me.get('discriminator')}")
    except Exception as e:
        print(f"[OAuth] token test failed: {e}")

    return redirect(url_for("form"))

@app.get("/logout")
def discord_logout():
    session.clear()
    return redirect(url_for("form"))

@app.get("/whoami")
def whoami():
    access = session.get("access_token")
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

    # Convert roles (skip @everyone and managed)
    roles = []
    for r in roles_json:
        if r.get("managed"):
            continue
        if r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # Categories and map
    categories = [c["name"] for c in channels_json if c.get("type") == 4]
    cat_map = {c["id"]: c["name"] for c in channels_json if c.get("type") == 4}

    # helper to map Discord API types -> our UI types
    def ui_type(api_type: int) -> str:
        # https://discord.com/developers/docs/resources/channel#channel-object-channel-types
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

    # Sort by 'position' so order is stable
    def pos(x): return x.get("position", 0)
    channels_sorted = sorted(channels_json, key=pos)

    chans = []
    for ch in channels_sorted:
        t = ch.get("type")
        name = ch.get("name", "")
        if t == 4:
            continue  # categories handled above
        parent_id = ch.get("parent_id")
        parent_name = cat_map.get(parent_id, "") if parent_id else ""
        t_ui = ui_type(t)
        topic = ch.get("topic", "")
        chans.append({
            "name": name,
            "type": t_ui,             # desired type (UI can change this)
            "original_type": t_ui,    # live type (for validation)
            "category": parent_name,
            "topic": topic
        })

    payload = {
        "mode": "update",
        "roles": roles,
        "categories": categories,
        "channels": chans
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
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>MessiahBot — Server Builder</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />

  <!-- SortableJS (vanilla) -->
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js" defer></script>

  <style>
    :root {
      --bg: #0f1221;
      --card: #151936;
      --muted: #99a3ff;
      --text: #e7e9ff;
      --border: #2b3070;
      --handle: #7a86ff;
      --accent: #c5c9ff;
    }
    * {
      box-sizing: border-box;
      -webkit-user-select: none; /* Safari fix: stop selecting text while dragging */
      user-select: none;
      -webkit-user-drag: none;   /* Safari fix: stop image/element drag ghosting */
    }
    body {
      margin: 0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: linear-gradient(180deg, #0f1221 0%, #0b0e1a 100%);
      color: var(--text);
    }
    .shell {
      max-width: 1100px;
      margin: 32px auto;
      padding: 0 16px 48px;
    }
    h1 {
      margin: 0 0 16px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }
    .grid {
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 16px;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 10px 24px rgba(0,0,0,0.25);
      overflow: hidden;
    }
    .card h2 {
      margin: 0;
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(255,255,255,0.02);
      font-size: 16px;
      letter-spacing: 0.3px;
      color: var(--accent);
    }

    .list {
      margin: 0;
      padding: 8px;
      list-style: none;
      min-height: 44px;
    }
    .item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      margin: 8px 0;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
    }
    .item.is-dragging {
      opacity: 0.85;
      transform: scale(1.01);
      box-shadow: 0 8px 20px rgba(0,0,0,0.35);
    }
    .ghost {
      opacity: 0.35;
    }
    .drag-handle {
      width: 16px;
      height: 16px;
      flex: 0 0 16px;
      border-radius: 4px;
      background: linear-gradient(180deg, var(--handle), #5460ff);
      box-shadow: inset 0 0 0 2px rgba(0,0,0,0.25);
      cursor: grab;
    }
    .drag-handle:active { cursor: grabbing; }

    .role-name, .category-name, .channel-name {
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-size: 14px;
    }
    .muted {
      color: var(--muted);
      font-size: 12px;
    }

    .category {
      margin: 8px;
      border: 1px dashed var(--border);
      border-radius: 14px;
      overflow: hidden;
    }
    .category-header {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      background: rgba(255,255,255,0.03);
      border-bottom: 1px solid var(--border);
    }
    .channels-list {
      padding: 8px;
      list-style: none;
      min-height: 38px;
    }

    .toolbar {
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      border-top: 1px solid var(--border);
      background: rgba(255,255,255,0.02);
    }
    button, .btn {
      appearance: none;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.04);
      color: var(--text);
      border-radius: 10px;
      padding: 8px 12px;
      font-size: 14px;
      cursor: pointer;
    }
    button:hover, .btn:hover {
      background: rgba(255,255,255,0.07);
    }

    .save-row {
      margin-top: 16px;
      display: flex;
      gap: 10px;
      align-items: center;
    }
    textarea#layout-json {
      width: 100%;
      min-height: 140px;
      background: #0c0f1d;
      color: #b7c0ff;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 12px;
      resize: vertical;
    }
  </style>
</head>

<body>
  <div class="shell">
    <h1>MessiahBot — Server Layout Builder</h1>
    <p class="muted">Drag to reorder roles, categories, and channels. Move channels between categories by dragging into another category’s list.</p>

    <div class="grid">
      <!-- ROLES -->
      <section class="card" id="roles-card">
        <h2>Roles (top = highest)</h2>
        <ul class="list" id="roles-list">
          <!-- Example items. On your server render, loop roles here with stable IDs -->
          <!--
          {% for r in roles %}
            <li class="item" data-role-id="{{ r.id }}">
              <span class="drag-handle" aria-hidden="true"></span>
              <span class="role-name">{{ r.name }}</span>
            </li>
          {% endfor %}
          -->
        </ul>
        <div class="toolbar">
          <button type="button" id="add-role">Add Role</button>
        </div>
      </section>

      <!-- CATEGORIES & CHANNELS -->
      <section class="card" id="cats-card">
        <h2>Categories & Channels</h2>

        <div id="categories-list">
          <!-- Each category contains its own channels list -->
          <!-- Example structure; on your server render categories + their channels -->
          <!--
          {% for c in categories %}
            <div class="category" data-category-id="{{ c.id }}">
              <div class="category-header item">
                <span class="drag-handle" aria-hidden="true"></span>
                <span class="category-name">{{ c.name }}</span>
              </div>
              <ul class="channels-list" data-category-id="{{ c.id }}">
                {% for ch in c.channels %}
                  <li class="item" data-channel-id="{{ ch.id }}">
                    <span class="drag-handle" aria-hidden="true"></span>
                    <span class="channel-name">{{ ch.name }}</span>
                  </li>
                {% endfor %}
              </ul>
            </div>
          {% endfor %}
          -->
        </div>

        <div class="toolbar">
          <button type="button" id="add-category">Add Category</button>
          <button type="button" id="add-channel-to-first">Add Channel to First Category</button>
        </div>
      </section>
    </div>

    <!-- SAVE / JSON MIRROR -->
    <div class="save-row">
      <form id="save-form" method="POST" action="/submit-server-layout" style="flex:1">
        <textarea id="layout-json" name="layout_json" readonly></textarea>
        <div style="margin-top:8px; display:flex; gap:8px; align-items:center;">
          <button type="submit">Save Layout</button>
          <span class="muted">JSON updates live as you drag.</span>
        </div>
      </form>
    </div>
  </div>

  <script>
    // ---- Example seed data (remove when server renders real data) ----
    const seed = {
      roles: [
        { id: "role_mod", name: "Moderator" },
        { id: "role_vip", name: "VIP" },
        { id: "role_member", name: "Member" }
      ],
      categories: [
        {
          id: "cat_announcements",
          name: "Announcements",
          channels: [
            { id: "ch_updates", name: "updates" },
            { id: "ch_schedule", name: "schedule" }
          ]
        },
        {
          id: "cat_general",
          name: "General",
          channels: [
            { id: "ch_chat", name: "chat" },
            { id: "ch_media", name: "media-share" }
          ]
        }
      ]
    };

    // Render the seed into DOM if lists are empty (dev/demo only)
    function mountSeed() {
      const rolesList = document.getElementById('roles-list');
      if (!rolesList.children.length) {
        seed.roles.forEach(r => {
          rolesList.insertAdjacentHTML('beforeend', `
            <li class="item" data-role-id="${r.id}">
              <span class="drag-handle" aria-hidden="true"></span>
              <span class="role-name">${r.name}</span>
            </li>
          `);
        });
      }
      const catsWrap = document.getElementById('categories-list');
      if (!catsWrap.children.length) {
        seed.categories.forEach(c => {
          const catHtml = `
            <div class="category" data-category-id="${c.id}">
              <div class="category-header item">
                <span class="drag-handle" aria-hidden="true"></span>
                <span class="category-name">${c.name}</span>
              </div>
              <ul class="channels-list" data-category-id="${c.id}"></ul>
            </div>
          `;
          catsWrap.insertAdjacentHTML('beforeend', catHtml);
          const ul = catsWrap.lastElementChild.querySelector('.channels-list');
          c.channels.forEach(ch => {
            ul.insertAdjacentHTML('beforeend', `
              <li class="item" data-channel-id="${ch.id}">
                <span class="drag-handle" aria-hidden="true"></span>
                <span class="channel-name">${ch.name}</span>
              </li>
            `);
          });
        });
      }
    }
    mountSeed();
    // ---- end seed ----

    // Utility: build JSON model from DOM
    function buildLayoutJSON() {
      const roles = [...document.querySelectorAll('#roles-list > .item')]
        .map((li, idx) => ({
          id: li.dataset.roleId,
          position: idx
        }));

      const categories = [...document.querySelectorAll('#categories-list > .category')].map((cat, cIdx) => {
        const catId = cat.dataset.categoryId;
        const name = cat.querySelector('.category-name')?.textContent?.trim() || '';
        const channels = [...cat.querySelectorAll('.channels-list > .item')].map((ch, chIdx) => ({
          id: ch.dataset.channelId,
          position: chIdx
        }));
        return { id: catId, name, position: cIdx, channels };
      });

      return { roles, categories };
    }

    // Mirror JSON to textarea
    function syncTextarea() {
      const ta = document.getElementById('layout-json');
      const json = buildLayoutJSON();
      ta.value = JSON.stringify(json, null, 2);
    }

    // Re-init channels Sortables after DOM changes
    function initChannelLists() {
      document.querySelectorAll('.channels-list').forEach(ul => {
        if (ul._sortableInitialized) return; // avoid double init
        ul._sortableInitialized = true;

        new Sortable(ul, {
          group: { name: 'channels', pull: true, put: true }, // cross-category moves
          handle: '.drag-handle',
          animation: 150,
          forceFallback: true,      // improves Safari reliability
          fallbackTolerance: 5,
          bubbleScroll: true,
          scroll: true,
          ghostClass: 'ghost',
          dragClass: 'is-dragging',
          onSort: syncTextarea,
          onAdd: syncTextarea,
          onRemove: syncTextarea,
          onEnd: syncTextarea
        });
      });
    }

    // Initialize Roles and Categories Sortables
    function initSortables() {
      // Roles (single list)
      const rolesList = document.getElementById('roles-list');
      new Sortable(rolesList, {
        handle: '.drag-handle',
        animation: 150,
        forceFallback: true,
        fallbackTolerance: 5,
        ghostClass: 'ghost',
        dragClass: 'is-dragging',
        onSort: syncTextarea,
        onEnd: syncTextarea
      });

      // Categories (container of .category cards)
      const catsContainer = document.getElementById('categories-list');
      new Sortable(catsContainer, {
        handle: '.category-header .drag-handle',
        animation: 150,
        forceFallback: true,
        fallbackTolerance: 5,
        ghostClass: 'ghost',
        dragClass: 'is-dragging',
        draggable: '.category',
        onSort: syncTextarea,
        onEnd: syncTextarea
      });

      // Channel lists inside each category
      initChannelLists();

      // Keep JSON updated initially
      syncTextarea();
    }

    // Call after Sortable library is loaded
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      initSortables();
    } else {
      window.addEventListener('DOMContentLoaded', initSortables);
    }

    // Demo add buttons (optional)
    document.getElementById('add-role')?.addEventListener('click', () => {
      const id = 'role_' + Math.random().toString(36).slice(2, 7);
      document.getElementById('roles-list').insertAdjacentHTML('beforeend', `
        <li class="item" data-role-id="${id}">
          <span class="drag-handle" aria-hidden="true"></span>
          <span class="role-name">New Role</span>
        </li>
      `);
      syncTextarea();
    });

    document.getElementById('add-category')?.addEventListener('click', () => {
      const id = 'cat_' + Math.random().toString(36).slice(2, 7);
      const html = `
        <div class="category" data-category-id="${id}">
          <div class="category-header item">
            <span class="drag-handle" aria-hidden="true"></span>
            <span class="category-name">New Category</span>
          </div>
          <ul class="channels-list" data-category-id="${id}"></ul>
        </div>
      `;
      document.getElementById('categories-list').insertAdjacentHTML('beforeend', html);
      initChannelLists(); // init Sortable on the new .channels-list
      syncTextarea();
    });

    document.getElementById('add-channel-to-first')?.addEventListener('click', () => {
      const first = document.querySelector('.channels-list');
      if (!first) return;
      const id = 'ch_' + Math.random().toString(36).slice(2, 7);
      first.insertAdjacentHTML('beforeend', `
        <li class="item" data-channel-id="${id}">
          <span class="drag-handle" aria-hidden="true"></span>
          <span class="channel-name">new-channel</span>
        </li>
      `);
      syncTextarea();
    });

    // Optional: handle form submit (Flask endpoint /submit-server-layout expects 'layout_json')
    document.getElementById('save-form')?.addEventListener('submit', (e) => {
      // Ensure JSON is up to date at submit
      syncTextarea();
      // default POST form submit — server should parse request.form['layout_json']
    });
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