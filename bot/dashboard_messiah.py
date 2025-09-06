# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # still shown in /dbcheck for visibility

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

# --- Reworked 'live' endpoint: use latest bot snapshot from DB -------------

@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    NO external Discord REST calls here.
    Returns the latest /snapshot_layout saved by the bot for this guild.
    This avoids Cloudflare rate limits from the Render web dyno.
    """
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({
            "ok": False,
            "error": "No snapshot found. In Discord, run /snapshot_layout first, then reload here."
        }), 404
    return jsonify({"ok": True, "source": "snapshot", "version": int(row["version"]), "payload": row["payload"]})

# --- API routes (DB-backed) -----------------------------------------------

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Only inserts a new version if the payload actually changed (de-dupe).
    Body JSON: { mode, roles[], categories[], channels[], prune?, renames?, community? }
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
    .note{color:#444}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <p class="note">
    <strong>Tip:</strong> To pull current server structure safely, run <code>/snapshot_layout</code> in your Discord server first,
    then click <strong>Load From Bot Snapshot</strong> below.
  </p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

    <p>
      <button type="button" id="loadLiveBtn">Load From Bot Snapshot</button>
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
                   <select name="channel_type"><option>text</option><option>voice</option><option>forum</option><option>announcement</option></select>
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
                       <option ${type==='announcement'?'selected':''}>announcement</option>
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
      for (let i=0; i<fromEls.length; i++){
        const from = (fromEls[i].value||"").trim();
        const to   = (toEls[i].value||"").trim();
        if (from && to) out.push({from,to});
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
      hydrateForm(data.payload || {});
      alert(`Loaded version ${data.version} from DB`);
    }

    // Load from Bot Snapshot (latest saved by /snapshot_layout)
    async function loadLive() {
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }
      const res = await fetch(`/api/live_layout/${gid}`);
      const data = await res.json();
      if (!data.ok) { alert(data.error || 'No snapshot found. Run /snapshot_layout in Discord, then retry.'); return; }
      hydrateForm(data.payload || {});
      alert(`Loaded from bot snapshot (version ${data.version || 'n/a'})`);
    }

    function hydrateForm(p) {
      // reset danger zone
      document.getElementById('prune_roles').checked = !!(p.prune && p.prune.roles);
      document.getElementById('prune_categories').checked = !!(p.prune && p.prune.categories);
      document.getElementById('prune_channels').checked = !!(p.prune && p.prune.channels);
      ['roleRenames','catRenames','chanRenames'].forEach(id => {
        const el = document.getElementById(id);
        while (el.firstChild) el.removeChild(el.firstChild);
      });
      const ren = (p.renames || {});
      (ren.roles || []).forEach(x => addRename('roleRenames', x.from||"", x.to||""));
      (ren.categories || []).forEach(x => addRename('catRenames', x.from||"", x.to||""));
      (ren.channels || []).forEach(x => addRename('chanRenames', x.from||"", x.to||""));

      const mode = (p.mode || 'build');
      const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
      if (radio) radio.checked = true;

      clearSection('roles');
      (p.roles || []).forEach(r => addRoleRow(r.name || "", r.color || "#000000"));
      if ((p.roles || []).length === 0) addRoleRow();

      clearSection('cats');
      // categories may be list of strings or objects with {name,...}
      (p.categories || []).forEach(c => {
        const name = (typeof c === 'string') ? c : (c && c.name) || "";
        addCatRow(name);
      });
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
