# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # needed to read live server via Discord REST

_psycopg_ok = False
try:
    import psycopg  # psycopg v3
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

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
    }
    code = 200 if (ok_env and ok_driver and ok_connect) else 500
    return status, code

# --- Live snapshot from Discord REST --------------------------------------

@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    Build a layout from the current live Discord server using the REST API.
    Requires DISCORD_BOT_TOKEN and that the bot is in the guild.
    """
    try:
        import requests
    except Exception:
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
    # Skip @everyone and managed roles (like integrations)
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
    # Build a map id->name for parent category lookup
    cat_map = {c["id"]: c["name"] for c in channels_json if c.get("type") == 4}

    chans = []
    # Sort by 'position' if present
    def pos(x): return x.get("position", 0)
    channels_sorted = sorted(channels_json, key=pos)

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
        # categories (type 4) are handled in categories list

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
    Body JSON: { mode, roles[], categories[], channels[] [, prune, renames] }
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

# --- Form UI ---------------------------------------------------------------

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
    .row{margin:6px 0; padding:6px; border:1px solid #ddd; border-radius:6px}
    .inline{display:inline-flex; align-items:center; gap:6px}
    .muted{color:#666; font-size:12px}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <p>Enter your Guild ID, then you can <strong>Load From Live Server</strong> or <strong>Load Latest From DB</strong>, edit, and <strong>Save Layout</strong>.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

    <p>
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
    <p class="muted"><em>These options can delete or rename live items. Use carefully.</em></p>
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

    <p>
      <!-- Keep inline onclick as a safety net -->
      <button type="button" id="saveBtn" onclick="saveLayout()">Save Layout</button>
    </p>
  </form>

  <script>
  // --- Boot / global error traps ------------------------------------------
  console.log('[Form] script booted');
  window.addEventListener('error', (e) => {
    console.error('Uncaught error:', e.error || e.message || e);
    alert('Form error: ' + (e.message || (e.error && e.error.message) || 'unknown'));
  });
  window.addEventListener('unhandledrejection', (e) => {
    console.error('Unhandled promise rejection:', e.reason);
    alert('Save failed: ' + (e.reason && e.reason.message ? e.reason.message : e.reason));
  });

  document.addEventListener('DOMContentLoaded', () => {
    console.log('[Form] DOM ready');

    // prevent Enter in inputs from submitting/refreshing
    document.getElementById('layoutForm').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        e.preventDefault();
      }
    });

    // Attach buttons (redundant with inline onclick, but nice)
    document.getElementById('loadLatestBtn')?.addEventListener('click', loadLatest);
    document.getElementById('loadLiveBtn')?.addEventListener('click', loadLive);
    document.getElementById('saveBtn')?.addEventListener('click', saveLayout);

    // seed one empty row for each section
    if (!document.querySelector('#roles > .row')) addRole();
    if (!document.querySelector('#cats > .row')) addCat();
    if (!document.querySelector('#chans > .row')) addChan();
  });

  // --- Small utils ---------------------------------------------------------
  function asStr(v){ return (v==null ? '' : String(v)); }
  function asBool(v){ return !!v; }
  function asColor(v){
    let s = asStr(v).trim();
    if (!s) return '#000000';
    if (s[0] !== '#') s = '#' + s;
    if (!/^#[0-9a-f]{6}$/i.test(s)) return '#000000';
    return s.toLowerCase();
  }
  function cleanName(v){
    if (v && typeof v === 'object' && 'name' in v) v = v.name;
    return asStr(v).trim();
  }

  // --- Row builders --------------------------------------------------------
  function moveRowUp(row){
    const prev = row?.previousElementSibling;
    if (!prev) return;
    row.parentElement.insertBefore(row, prev);
  }
  function moveRowDown(row){
    const next = row?.nextElementSibling;
    if (!next) return;
    row.parentElement.insertBefore(next, row);
  }

  function addRole(name = "", color = "#000000"){
    const d = document.createElement('div');
    d.className = 'row';
    d.innerHTML = `
      <div class="inline">
        <button type="button" title="Up" onclick="moveRowUp(this.closest('.row'))">↑</button>
        <button type="button" title="Down" onclick="moveRowDown(this.closest('.row'))">↓</button>
        <input placeholder="Role" name="role_name" value="${cleanName(name)}">
        <input type="color" name="role_color" value="${asColor(color)}">
        <button type="button" title="Remove" onclick="this.closest('.row').remove()">✕</button>
      </div>`;
    document.getElementById('roles').appendChild(d);
  }

  function addCat(name = ""){
    const d = document.createElement('div');
    d.className = 'row';
    d.dataset.name = cleanName(name);
    d.innerHTML = `
      <div class="inline">
        <button type="button" title="Up" onclick="moveRowUp(this.closest('.row'))">↑</button>
        <button type="button" title="Down" onclick="moveRowDown(this.closest('.row'))">↓</button>
        <input placeholder="Category" name="category" value="${cleanName(name)}"
               oninput="this.closest('.row').dataset.name = this.value.trim()">
        <button type="button" title="Remove" onclick="this.closest('.row').remove()">✕</button>
      </div>`;
    document.getElementById('cats').appendChild(d);
  }

  function addChan(name = "", type = "text", category = ""){
    const d = document.createElement('div');
    d.className = 'row';
    d.dataset.name = cleanName(name);
    d.dataset.type = ['text','voice','forum'].includes((type||'').toLowerCase()) ? type.toLowerCase() : 'text';
    d.dataset.category = cleanName(category);
    d.innerHTML = `
      <div class="inline">
        <button type="button" title="Up" onclick="moveRowUp(this.closest('.row'))">↑</button>
        <button type="button" title="Down" onclick="moveRowDown(this.closest('.row'))">↓</button>
        <input placeholder="Channel" name="channel_name" value="${cleanName(name)}"
               oninput="this.closest('.row').dataset.name = this.value.trim()">
        <select name="channel_type" onchange="this.closest('.row').dataset.type = this.value">
          <option ${d.dataset.type==='text' ? 'selected':''}>text</option>
          <option ${d.dataset.type==='voice'? 'selected':''}>voice</option>
          <option ${d.dataset.type==='forum'? 'selected':''}>forum</option>
        </select>
        <input placeholder="Parent Category" name="channel_category" value="${cleanName(category)}"
               oninput="this.closest('.row').dataset.category = this.value.trim()">
        <button type="button" title="Remove" onclick="this.closest('.row').remove()">✕</button>
      </div>`;
    document.getElementById('chans').appendChild(d);
  }

  // --- Renames UI ----------------------------------------------------------
  function addRename(containerId, fromVal="", toVal="") {
    const d = document.createElement('div');
    d.className = 'row';
    d.innerHTML = `
      <div class="inline">
        <input placeholder="From name" class="rename_from" value="${cleanName(fromVal)}">
        <span>→</span>
        <input placeholder="To name" class="rename_to" value="${cleanName(toVal)}">
        <button type="button" title="Remove" onclick="this.closest('.row').remove()">✕</button>
      </div>`;
    document.getElementById(containerId).appendChild(d);
  }
  function collectRenames(containerId){
    const out = [];
    document.querySelectorAll('#'+containerId+' .row').forEach(d => {
      const from = cleanName(d.querySelector('.rename_from')?.value);
      const to   = cleanName(d.querySelector('.rename_to')?.value);
      if (from && to) out.push({from, to});
    });
    return out;
  }

  // --- Hydration helpers ---------------------------------------------------
  function clearSection(id) {
    const el = document.getElementById(id);
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function hydrateForm(p) {
    // Reset toggles + rename boxes
    ['prune_roles','prune_categories','prune_channels'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.checked = false;
    });
    ['roleRenames','catRenames','chanRenames'].forEach(id => clearSection(id));

    const mode = (p.mode || 'build');
    const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    clearSection('roles');
    (p.roles || []).forEach(r => addRole(cleanName(r.name), asColor(r.color)));
    if ((p.roles || []).length === 0) addRole();

    clearSection('cats');
    (p.categories || []).forEach(c => addCat(cleanName(c)));
    if ((p.categories || []).length === 0) addCat();

    clearSection('chans');
    (p.channels || []).forEach(ch => addChan(cleanName(ch.name), asStr(ch.type).toLowerCase(), cleanName(ch.category)));
    if ((p.channels || []).length === 0) addChan();
  }

  // --- Loaders -------------------------------------------------------------
  async function loadLatest() {
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) { alert('Enter Guild ID'); return; }
    const res = await fetch(`/api/layout/${gid}/latest`);
    const data = await res.json();
    if (!data.ok) { alert(data.error || 'No layout'); return; }
    hydrateForm(data.payload || {});
    alert(`Loaded version ${data.version} from DB`);
  }

  async function loadLive() {
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) { alert('Enter Guild ID'); return; }
    const res = await fetch(`/api/live_layout/${gid}`);
    const data = await res.json();
    if (!data.ok) { alert(data.error || 'Failed to load live server'); return; }
    hydrateForm(data.payload || {});
    alert('Loaded from live server');
  }

  // --- Save (HARDENED) -----------------------------------------------------
  async function saveLayout(){
    console.log('[Form] saveLayout start');
    try{
      const form = document.getElementById('layoutForm');
      const gid  = asStr(document.getElementById('guild_id')?.value).trim();
      if (!gid) { alert('Enter Guild ID'); return; }

      // ROLES
      const roles = [];
      document.querySelectorAll('#roles > .row').forEach(row => {
        const name  = cleanName(row.querySelector('input[name="role_name"]')?.value);
        const color = asColor(row.querySelector('input[name="role_color"]')?.value);
        if (name) roles.push({ name, color });
      });

      // CATEGORIES
      const categories = [];
      document.querySelectorAll('#cats > .row').forEach(row => {
        const raw = row.querySelector('input[name="category"]')?.value ?? row.dataset?.name;
        const name = cleanName(raw);
        if (name) categories.push(name);
      });

      // CHANNELS
      const channels = [];
      document.querySelectorAll('#chans > .row').forEach(row => {
        const name = cleanName(row.querySelector('input[name="channel_name"]')?.value ?? row.dataset?.name);
        let type   = asStr(row.querySelector('select[name="channel_type"]')?.value ?? row.dataset?.type).toLowerCase();
        if (!['text','voice','forum'].includes(type)) type = 'text';
        const category = cleanName(row.querySelector('input[name="channel_category"]')?.value ?? row.dataset?.category);
        if (name) channels.push({ name, type, category });
      });

      const mode = form?.mode?.value || 'build';

      const payload = {
        mode, roles, categories, channels,
        prune: {
          roles: !!document.getElementById('prune_roles')?.checked,
          categories: !!document.getElementById('prune_categories')?.checked,
          channels: !!document.getElementById('prune_channels')?.checked
        },
        renames: {
          roles: collectRenames('roleRenames'),
          categories: collectRenames('catRenames'),
          channels: collectRenames('chanRenames')
        }
      };

      const raw = JSON.stringify(payload);
      console.log('[Form] payload bytes', raw.length, payload);

      const res = await fetch(`/api/layout/${gid}`, {
        method: 'POST',
        headers: { 'Content-Type':'application/json' },
        body: raw
      });
      if (!res.ok){
        const t = await res.text();
        console.error('Save failed', res.status, t);
        alert(`Save failed ${res.status}: ${t}`);
        return;
      }
      const data = await res.json();
      console.log('Save response', data);
      if (data.ok && data.no_change){
        alert(`No changes detected. Current version is still ${data.version}.`);
      }else if (data.ok){
        alert(`Saved version ${data.version}`);
      }else{
        alert(data.error || 'Unknown server error');
      }
    }catch(err){
      console.error('saveLayout exception', err);
      alert('Save crashed: ' + (err?.message || err));
    }
  }
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

# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    # Local dev runner. On Render, you can also use:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))