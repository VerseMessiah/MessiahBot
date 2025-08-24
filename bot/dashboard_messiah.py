# bot/dashboard_messiah.py
import os
import json
import time
import secrets
import urllib.parse

from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # used by /api/live_layout
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
DASHBOARD_BASE_URL = os.getenv("DASHBOARD_BASE_URL")  # e.g. https://your-app.onrender.com

_psycopg_ok = False
try:
    import psycopg  # psycopg v3
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

# requests is only used on the web service for Twitch OAuth + Discord REST in /live_layout
try:
    import requests  # ensure 'requests' is in requirements.txt
except Exception:
    requests = None

app = Flask(__name__)

# --- Helpers ---------------------------------------------------------------

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
        "User-Agent": "MessiahBotDashboard (https://example, 1.0)",
        "Content-Type": "application/json",
    }

# --- Probe / utility routes ------------------------------------------------

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
        import psycopg  # noqa: F401
        ok_driver = True
        driver_version = psycopg.__version__
    except Exception:
        ok_driver = False
        driver_version = None

    ok_connect = False
    if ok_env and ok_driver:
        try:
            with psycopg.connect(DATABASE_URL, sslmode="require") as _conn:
                ok_connect = True
        except Exception:
            ok_connect = False

    status = {
        "database_url_present": ok_env,
        "psycopg_available": ok_driver,
        "psycopg_version": driver_version,
        "can_connect": ok_connect,
        "has_discord_bot_token": bool(DISCORD_BOT_TOKEN),
        "has_twitch_oauth_vars": bool(TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET and DASHBOARD_BASE_URL),
    }
    code = 200 if (ok_env and ok_driver and ok_connect) else 500
    return status, code

# --- NEW: Live snapshot from Discord REST ---------------------------------

@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    Build a layout from the current live Discord server using the REST API.
    Requires DISCORD_BOT_TOKEN and that the bot is in the guild.
    """
    if requests is None:
        return jsonify({"ok": False, "error": "Python 'requests' not installed; add requests to requirements.txt"}), 500
    if not DISCORD_BOT_TOKEN:
        return jsonify({"ok": False, "error": "DISCORD_BOT_TOKEN not set on web service"}), 500

    base = "https://discord.com/api/v10"
    headers = _discord_headers()

    # roles
    r_roles = requests.get(f"{base}/guilds/{guild_id}/roles", headers=headers, timeout=20)
    if r_roles.status_code == 403:
        return jsonify({"ok": False, "error": "Forbidden: bot lacks permission or is not in this guild"}), 403
    if r_roles.status_code == 404:
        return jsonify({"ok": False, "error": "Guild not found (check Guild ID)"}), 404
    if r_roles.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord roles error {r_roles.status_code}: {r_roles.text}"}), 502
    roles_json = r_roles.json()

    # channels
    r_channels = requests.get(f"{base}/guilds/{guild_id}/channels", headers=headers, timeout=20)
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord channels error {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    # Convert to our schema
    roles = []
    for r in roles_json:
        if r.get("managed"):
            continue
        if r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # Channels: types → 0=text, 2=voice, 4=category, 15=forum
    categories = [c["name"] for c in channels_json if c.get("type") == 4]
    cat_map = {c["id"]: c["name"] for c in channels_json if c.get("type") == 4}

    def pos(x): return x.get("position", 0)
    channels_sorted = sorted(channels_json, key=pos)

    chans = []
    for ch in channels_sorted:
        t = ch.get("type")
        name = ch.get("name", "")
        parent_id = ch.get("parent_id")
        parent_name = cat_map.get(parent_id, "") if parent_id else ""
        if t == 0:   # text
            chans.append({"name": name, "type": "text", "category": parent_name})
        elif t == 2: # voice
            chans.append({"name": name, "type": "voice", "category": parent_name})
        elif t == 15:  # forum
            chans.append({"name": name, "type": "forum", "category": parent_name})

    payload = {
        "mode": "update",
        "roles": roles,
        "categories": categories,
        "channels": chans
    }
    return jsonify({"ok": True, "payload": payload})

# --- API routes (DB-backed) -----------------------------------------------

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Only inserts a new version if the payload actually changed (de-dupe).
    Body JSON: { mode, roles[], categories[], channels[], prune?, renames?, deletions? }
    """
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    incoming = request.get_json(silent=True) or {}
    if not incoming:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    latest = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if latest and _json_equal(incoming, latest["payload"]):
        return jsonify({"ok": True, "version": int(latest["version"]), "no_change": True})

    row = _db_one(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM builder_layouts WHERE guild_id = %s",
        (guild_id,),
    )
    version = int((row or {}).get("v", 1))

    _db_exec(
        "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s, %s, %s::jsonb)",
        (guild_id, version, json.dumps(incoming)),
    )
    return jsonify({"ok": True, "version": version, "no_change": False})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id: str):
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No layout"}), 404
    return jsonify({"ok": True, "version": int(row["version"]), "payload": row["payload"]})

# --- Twitch OAuth + per-guild settings ------------------------------------

def _oauth_state_pack(guild_id: str) -> str:
    # simple state: guild|nonce|ts
    return f"{guild_id}|{secrets.token_urlsafe(16)}|{int(time.time())}"

def _oauth_state_unpack(state: str) -> str:
    return (state or "").split("|", 1)[0]

def _twitch_authorize_url(state: str) -> str:
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "redirect_uri": f"{DASHBOARD_BASE_URL}/oauth/twitch/callback",
        "response_type": "code",
        "scope": "channel:read:schedule channel:manage:schedule",
        "state": state,
        "force_verify": "true",
    }
    return "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode(params)

def _twitch_token(code: str) -> dict:
    if requests is None:
        raise RuntimeError("requests not installed")
    url = "https://id.twitch.tv/oauth2/token"
    params = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": f"{DASHBOARD_BASE_URL}/oauth/twitch/callback",
    }
    r = requests.post(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()

def _twitch_get_user(access_token: str) -> dict:
    if requests is None:
        raise RuntimeError("requests not installed")
    r = requests.get(
        "https://api.twitch.tv/helix/users",
        headers={"Client-Id": TWITCH_CLIENT_ID, "Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json().get("data") or []
    return data[0] if data else {}

def _db_upsert_oauth(gid: str, user: dict, token: dict):
    scope = token.get("scope") or []
    expires_at = int(time.time()) + int(token.get("expires_in", 0))
    expires_at_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires_at))
    _db_exec(
        """
        INSERT INTO twitch_oauth (guild_id, broadcaster_id, login, access_token, refresh_token, scope, token_expires_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (guild_id) DO UPDATE SET
            broadcaster_id=EXCLUDED.broadcaster_id,
            login=EXCLUDED.login,
            access_token=EXCLUDED.access_token,
            refresh_token=EXCLUDED.refresh_token,
            scope=EXCLUDED.scope,
            token_expires_at=EXCLUDED.token_expires_at
        """,
        (gid, user.get("id"), user.get("login"), token.get("access_token"), token.get("refresh_token"), scope, expires_at_iso),
    )
    # Ensure settings row exists
    _db_exec(
        "INSERT INTO schedule_sync_settings (guild_id, enabled) VALUES (%s,true) ON CONFLICT (guild_id) DO NOTHING",
        (gid,),
    )

def _db_set_sync_login(gid: str, login: str):
    _db_exec(
        """
        INSERT INTO schedule_sync_settings (guild_id, tz, enabled)
        VALUES (%s, 'America/Denver', true)
        ON CONFLICT (guild_id) DO NOTHING
        """,
        (gid,),
    )
    _db_exec(
        "UPDATE schedule_sync_settings SET category_map_json = jsonb_set(COALESCE(category_map_json,'{}'::jsonb), '{twitch_login}', %s::jsonb, true) WHERE guild_id=%s",
        (json.dumps(login), gid),
    )

@app.get("/api/twitch/status/<guild_id>")
def twitch_status(guild_id: str):
    row = _db_one("SELECT * FROM twitch_oauth WHERE guild_id=%s", (guild_id,))
    settings = _db_one("SELECT category_map_json FROM schedule_sync_settings WHERE guild_id=%s", (guild_id,))
    return jsonify({
        "ok": True,
        "connected": bool(row),
        "login": (row or {}).get("login"),
        "broadcaster_id": (row or {}).get("broadcaster_id"),
        "preferred_login": ((settings or {}).get("category_map_json") or {}).get("twitch_login")
    })

@app.post("/api/twitch/save_login")
def twitch_save_login():
    body = request.get_json(silent=True) or {}
    gid = (body.get("guild_id") or "").strip()
    login = (body.get("login") or "").strip().lower()
    if not gid or not login:
        return jsonify({"ok": False, "error": "guild_id and login required"}), 400
    _db_set_sync_login(gid, login)
    return jsonify({"ok": True})

@app.get("/oauth/twitch/start")
def twitch_start():
    guild_id = request.args.get("guild_id","").strip()
    if not guild_id or not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET or not DASHBOARD_BASE_URL:
        return "Missing guild_id or server OAuth config.", 400
    state = _oauth_state_pack(guild_id)
    return "", 302, {"Location": _twitch_authorize_url(state)}

@app.get("/oauth/twitch/callback")
def twitch_callback():
    code = request.args.get("code")
    state = request.args.get("state") or ""
    guild_id = _oauth_state_unpack(state)
    if not code or not guild_id:
        return "Invalid OAuth response.", 400
    try:
        token = _twitch_token(code)
        user = _twitch_get_user(token["access_token"])
        if not user:
            return "Could not resolve Twitch user.", 400
        _db_upsert_oauth(guild_id, user, token)
        html = f"<h2>✅ Connected Twitch @{user.get('login')}</h2><p>You can close this window and return to the dashboard.</p>"
        return html, 200, {"Content-Type": "text/html"}
    except Exception as e:
        return f"OAuth error: {e}", 500

# --- Form UI (Server Builder) ---------------------------------------------

_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Submit Server Layout</title>
  <style>
    body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px}
    button{margin-top:6px}
    .row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <p><a href="/">Home</a> · <strong>Server Builder</strong> · <a href="/schedule">Events & Twitch</a></p>
  <p>Enter your Guild ID, then you can <strong>Load From Live Server</strong> or <strong>Load Latest From DB</strong>, edit, and <strong>Save Layout</strong>.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

    <p class="row">
      <button type="button" id="loadLiveBtn">Load From Live Server</button>
      <button type="button" id="loadLatestBtn">Load Latest From DB</button>
    </p>

    <fieldset>
      <legend>Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <h3>Roles</h3>
    <div id="roles"></div>
    <button type="button" onclick="addRole()">Add Role</button>

    <h3>Categories</h3>
    <div id="cats"></div>
    <button type="button" onclick="addCat()">Add Category</button>

    <h3>Channels</h3>
    <div id="chans"></div>
    <button type="button" onclick="addChan()">Add Channel</button>

    <h3>Danger Zone</h3>
    <p><em>These options can delete or rename live items. Use carefully.</em></p>
    <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
    <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
    <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>

    <h4>Role Renames</h4>
    <div id="roleRenames"></div>
    <button type="button" onclick="addRename('roleRenames')">Add Role Rename</button>

    <h4>Category Renames</h4>
    <div id="catRenames"></div>
    <button type="button" onclick="addRename('catRenames')">Add Category Rename</button>

    <h4>Channel Renames</h4>
    <div id="chanRenames"></div>
    <button type="button" onclick="addRename('chanRenames')">Add Channel Rename</button>

    <p><button type="button" id="saveBtn">Save Layout</button></p>
  </form>

  <script>
    // Prevent Enter from auto-submitting the form
    document.getElementById('layoutForm').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        e.preventDefault();
      }
    });

    // Builders
    function addRole(){
      const d=document.createElement('div');
      d.innerHTML=`<input placeholder="Role" name="role_name">
                   <input type="color" name="role_color" value="#000000">
                   <button type=button onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('roles').appendChild(d);
    }
    function addCat(){
      const d=document.createElement('div');
      d.innerHTML=`<input placeholder="Category" name="category">
                   <button type=button onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('cats').appendChild(d);
    }
    function addChan(){
      const d=document.createElement('div');
      d.innerHTML=`<input placeholder="Channel" name="channel_name">
                   <select name="channel_type"><option>text</option><option>voice</option><option>forum</option></select>
                   <input placeholder="Parent Category" name="channel_category">
                   <button type=button onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('chans').appendChild(d);
    }

    // Prefill helpers
    function clearSection(id) {
      const el = document.getElementById(id);
      while (el.firstChild) el.removeChild(el.firstChild);
    }
    function addRoleRow(name = "", color = "#000000") {
      const d = document.createElement('div');
      d.innerHTML = `<input placeholder="Role" name="role_name" value="${name}">
                     <input type="color" name="role_color" value="${color || '#000000'}">
                     <button type="button" onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('roles').appendChild(d);
    }
    function addCatRow(name = "") {
      const d = document.createElement('div');
      d.innerHTML = `<input placeholder="Category" name="category" value="${name}">
                     <button type="button" onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('cats').appendChild(d);
    }
    function addChanRow(name = "", type = "text", category = "") {
      const d = document.createElement('div');
      d.innerHTML = `<input placeholder="Channel" name="channel_name" value="${name}">
                     <select name="channel_type">
                       <option ${type==='text'?'selected':''}>text</option>
                       <option ${type==='voice'?'selected':''}>voice</option>
                       <option ${type==='forum'?'selected':''}>forum</option>
                     </select>
                     <input placeholder="Parent Category" name="channel_category" value="${category}">
                     <button type="button" onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('chans').appendChild(d);
    }
    function addRename(containerId, fromVal="", toVal="") {
      const d = document.createElement('div');
      d.innerHTML = `<input placeholder="From name" class="rename_from" value="${fromVal}">
                     <span>→</span>
                     <input placeholder="To name" class="rename_to" value="${toVal}">
                     <button type="button" onclick="this.parentElement.remove()">x</button>`;
      document.getElementById(containerId).appendChild(d);
    }
    function collectRenames(containerId){
      const out = [];
      const fromEls = document.querySelectorAll(`#${containerId} .rename_from`);
      const toEls = document.querySelectorAll(`#${containerId} .rename_to`);
      for (let i = 0; i < fromEls.length; i++) {
        const from = (fromEls[i].value || "").trim();
        const to = (toEls[i].value || "").trim();
        if (from && to) out.push({from, to});
      }
      return out;
    }

    // Load latest from DB
    async function loadLatest() {
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }
      const res = await fetch(`/api/layout/${gid}/latest`);
      const data = await res.json();
      if (!data.ok) { alert(data.error || 'No layout'); return; }
      const p = data.payload || {};
      hydrateForm(p);
      alert(`Loaded version ${data.version} from DB`);
    }

    // Load from live Discord server
    async function loadLive() {
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }
      const res = await fetch(`/api/live_layout/${gid}`);
      const data = await res.json();
      if (!data.ok) { alert(data.error || 'Failed to load live server'); return; }
      hydrateForm(data.payload || {});
      alert('Loaded from live server');
    }

    function hydrateForm(p) {
      // Clear danger-zone state
      document.getElementById('prune_roles').checked = false;
      document.getElementById('prune_categories').checked = false;
      document.getElementById('prune_channels').checked = false;
      ['roleRenames','catRenames','chanRenames'].forEach(id => {
        const el = document.getElementById(id);
        while (el.firstChild) el.removeChild(el.firstChild);
      });

      const mode = (p.mode || 'build');
      const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
      if (radio) radio.checked = true;

      clearSection('roles');
      (p.roles || []).forEach(r => addRoleRow(r.name || "", r.color || "#000000"));
      if ((p.roles || []).length === 0) addRoleRow();

      clearSection('cats');
      (p.categories || []).forEach(c => addCatRow(c));
      if ((p.categories || []).length === 0) addCatRow();

      clearSection('chans');
      (p.channels || []).forEach(ch => addChanRow(ch.name || "", (ch.type||'text'), ch.category || ""));
      if ((p.channels || []).length === 0) addChanRow();
    }

    document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
    document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

    // Save layout
    async function saveLayout(){
      const form = document.getElementById('layoutForm');
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }

      const roles = [], categories = [], channels = [];
      const mode = form.mode.value;

      const rn = form.querySelectorAll('input[name="role_name"]');
      const rc = form.querySelectorAll('input[name="role_color"]');
      for (let i = 0; i < rn.length; i++) {
        if (rn[i].value) roles.push({ name: rn[i].value, color: rc[i].value });
      }
      form.querySelectorAll('input[name="category"]').forEach(el => {
        if (el.value) categories.push(el.value);
      });
      const cn = form.querySelectorAll('input[name="channel_name"]');
      const ct = form.querySelectorAll('select[name="channel_type"]');
      const cc = form.querySelectorAll('input[name="channel_category"]');
      for (let i = 0; i < cn.length; i++) {
        if (cn[i].value) channels.push({ name: cn[i].value, type: ct[i].value, category: cc[i].value });
      }

      const prune_roles = document.getElementById('prune_roles').checked;
      const prune_categories = document.getElementById('prune_categories').checked;
      const prune_channels = document.getElementById('prune_channels').checked;

      const roles_rename = collectRenames('roleRenames');
      const categories_rename = collectRenames('catRenames');
      const channels_rename = collectRenames('chanRenames');

      const payload = {
        mode,
        roles, categories, channels,
        prune: { roles: prune_roles, categories: prune_categories, channels: prune_channels },
        renames: { roles: roles_rename, categories: categories_rename, channels: channels_rename }
      };

      const res = await fetch(`/api/layout/${gid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.ok && data.no_change) {
        alert(`No changes detected. Current version is still ${data.version}.`);
      } else if (data.ok) {
        alert(`Saved version ${data.version}`);
      } else {
        alert(data.error || 'Error');
      }
    }
    document.getElementById('saveBtn').addEventListener('click', saveLayout);
  </script>
</body>
</html>
"""

# --- Separate Schedule/Twitch page ----------------------------------------

@app.get("/schedule")
def schedule_config():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>MessiahBot — Events & Twitch Sync</title>
      <style>
        body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 12px}
        fieldset{margin:16px 0;padding:12px;border-radius:8px}
        button{margin-top:6px}
        .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
      </style>
    </head>
    <body>
      <h1>📅 MessiahBot — Events & Twitch Sync</h1>
      <p><a href="/">Home</a> · <a href="/form">Server Builder</a> · <strong>Events & Twitch</strong></p>

      <fieldset>
        <legend>Guild</legend>
        <div class="row">
          <label>Guild ID <input type="text" id="guild_id" placeholder="123456789" required></label>
          <button type="button" id="status_btn">Check Status</button>
          <span id="status_text" style="font-weight:bold"></span>
        </div>
      </fieldset>

      <fieldset>
        <legend>Twitch</legend>
        <div class="row">
          <label>Twitch Channel <input type="text" id="tw_login" placeholder="twitchusername"></label>
          <button type="button" id="save_login_btn">Save Channel</button>
          <button type="button" id="connect_btn">Connect Twitch (OAuth)</button>
        </div>
        <small>Channel is optional if you connect via OAuth; we store the connected login/broadcaster per guild.</small>
      </fieldset>

      <fieldset>
        <legend>Discord Defaults</legend>
        <div class="row">
          <label>Default Event Channel: set in Discord via <code>/schedule_sync_set_channel</code></label>
        </div>
        <div class="row">
          <label>Time Zone: set via <code>/schedule_sync_set_tz</code></label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Sync Controls</legend>
        <div class="row">
          <p>Enable/disable and run sync from Discord with <code>/schedule_sync enable|disable|now</code>.</p>
        </div>
      </fieldset>

      <script>
        async function getJSON(url, opts={}){ const r = await fetch(url, opts); return r.json(); }

        async function refreshStatus(){
          const gid = document.getElementById('guild_id').value.trim();
          if (!gid) return;
          const data = await getJSON(`/api/twitch/status/${gid}`);
          const el = document.getElementById('status_text');
          if (data.connected){
            el.textContent = `Connected as @${data.login} (id ${data.broadcaster_id})`;
            if (data.preferred_login) document.getElementById('tw_login').value = data.preferred_login;
          } else {
            el.textContent = 'Not connected';
          }
        }

        document.getElementById('status_btn').onclick = refreshStatus;

        document.getElementById('save_login_btn').onclick = async () => {
          const gid = document.getElementById('guild_id').value.trim();
          const login = document.getElementById('tw_login').value.trim().toLowerCase();
          if (!gid || !login) return alert('Enter Guild ID and Twitch channel');
          const data = await getJSON('/api/twitch/save_login', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({guild_id: gid, login})
          });
          if (data.ok) { alert('Saved'); refreshStatus(); } else alert(data.error||'Error');
        };

        document.getElementById('connect_btn').onclick = () => {
          const gid = document.getElementById('guild_id').value.trim();
          if (!gid) return alert('Enter Guild ID');
          window.location.href = `/oauth/twitch/start?guild_id=${encodeURIComponent(gid)}`;
        };
      </script>
    </body>
    </html>
    """
    return html, 200, {"Content-Type":"text/html"}

# --- Home + Form routes ----------------------------------------------------

@app.get("/")
def index():
    return (
        '<h1>MessiahBot Dashboard</h1>'
        '<p><a href="/form">Server Builder</a> · <a href="/schedule">Events & Twitch</a></p>',
        200,
        {"Content-Type": "text/html"},
    )

@app.get("/form")
def form():
    return render_template_string(_FORM_HTML)

# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    # Local dev runner. On Render, you can also use:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
