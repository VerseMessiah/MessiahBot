# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

_psycopg_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

app = Flask(__name__)

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
        "User-Agent": "MessiahBotDashboard (1.0)",
        "Content-Type": "application/json",
    }

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

@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    try:
        import requests
    except Exception:
        return jsonify({"ok": False, "error": "Python 'requests' not installed; add requests==2.32.3"}), 500

    if not DISCORD_BOT_TOKEN:
        return jsonify({"ok": False, "error": "DISCORD_BOT_TOKEN not set on web service"}), 500

    base = "https://discord.com/api/v10"
    headers = _discord_headers()

    r_roles = requests.get(f"{base}/guilds/{guild_id}/roles", headers=headers, timeout=20)
    if r_roles.status_code >= 400:
        return jsonify({"ok": False, "error": f"roles {r_roles.status_code}: {r_roles.text}"}), 502
    roles_json = r_roles.json()

    r_channels = requests.get(f"{base}/guilds/{guild_id}/channels", headers=headers, timeout=20)
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"channels {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    roles = []
    for r in roles_json:
        if r.get("managed") or r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

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
        if t == 0:
            chans.append({"name": name, "type": "text", "category": parent_name})
        elif t == 2:
            chans.append({"name": name, "type": "voice", "category": parent_name})
        elif t == 15:
            chans.append({"name": name, "type": "forum", "category": parent_name})

    payload = {"mode": "update", "roles": roles, "categories": categories, "channels": chans}
    return jsonify({"ok": True, "payload": payload})

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
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

    row = _db_one("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM builder_layouts WHERE guild_id = %s", (guild_id,))
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

_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Submit Server Layout</title>
  <style>
    body{font-family:sans-serif;max-width:980px;margin:24px auto;padding:0 12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px}
    button{margin-top:6px}
    .row{display:flex;gap:8px;align-items:center;margin:6px 0}
    .row > input[type="text"]{min-width:200px}
    .small{font-size:12px;color:#666}
    .pill{padding:2px 8px;border:1px solid #ddd;border-radius:999px}
    .danger{color:#a00}
    .muted{opacity:0.7}
    .delmark{outline:1px dashed #c33;padding:6px;border-radius:6px}
    .renamebox{outline:1px dashed #338;padding:6px;border-radius:6px}
    select{min-width:150px}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <p>Enter your Guild ID, then <strong>Load From Live Server</strong> or <strong>Load Latest From DB</strong>, edit, and <strong>Save Layout</strong>.</p>

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
    <p class="small">Per-item controls below let you mark deletions or renames. If you delete a category that still has channels, you must choose a reassignment category.</p>
    <p class="small"><strong>Global prune (optional):</strong> delete anything in the server that is <em>not</em> listed in this layout.</p>
    <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
    <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
    <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>

    <p><button type="button" id="saveBtn">Save Layout</button></p>
  </form>

  <script>
    // Prevent Enter from auto-submitting the form
    document.getElementById('layoutForm').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') e.preventDefault();
    });

    // ---------- Row builders with per-item rename/delete ----------
    function roleRow(name="", color="#000000", renameTo="", del=false){
      const d = document.createElement('div'); d.className = 'row';
      d.innerHTML = `
        <input placeholder="Role" name="role_name" value="${name}">
        <input type="color" name="role_color" value="${color || '#000000'}">
        <label class="pill"><input type="checkbox" class="role_rename"> rename</label>
        <input class="role_rename_to renamebox" placeholder="New name" style="display:none" value="${renameTo}">
        <label class="pill danger"><input type="checkbox" class="role_delete"> delete</label>
        <button type="button" onclick="this.parentElement.remove()">x</button>`;
      // toggle rename input
      const cb = d.querySelector('.role_rename');
      const to = d.querySelector('.role_rename_to');
      cb.addEventListener('change', ()=>{ to.style.display = cb.checked ? 'inline-block' : 'none'; });
      if (renameTo) { cb.checked = true; to.style.display='inline-block'; }
      if (del) d.classList.add('delmark');
      return d;
    }

    function catRow(name="", renameTo="", del=false){
      const d = document.createElement('div'); d.className = 'row';
      d.innerHTML = `
        <input placeholder="Category" name="category" value="${name}">
        <label class="pill"><input type="checkbox" class="cat_rename"> rename</label>
        <input class="cat_rename_to renamebox" placeholder="New name" style="display:none" value="${renameTo}">
        <label class="pill danger"><input type="checkbox" class="cat_delete"> delete</label>
        <span class="small muted">If deleting, reassign channels to:</span>
        <select class="cat_reassign" disabled></select>
        <button type="button" onclick="this.parentElement.remove()">x</button>`;
      const cb = d.querySelector('.cat_rename');
      const to = d.querySelector('.cat_rename_to');
      cb.addEventListener('change', ()=>{ to.style.display = cb.checked ? 'inline-block' : 'none'; });
      if (renameTo) { cb.checked = true; to.style.display='inline-block'; }
      const delCb = d.querySelector('.cat_delete');
      const reSel = d.querySelector('.cat_reassign');
      delCb.addEventListener('change', ()=>{ reSel.disabled = !delCb.checked; refreshReassignOptions(); if (delCb.checked) d.classList.add('delmark'); else d.classList.remove('delmark'); });
      if (del) { delCb.checked = true; reSel.disabled = false; d.classList.add('delmark'); }
      return d;
    }

    function chanRow(name="", type="text", category="", renameTo="", del=false){
      const d = document.createElement('div'); d.className = 'row';
      d.innerHTML = `
        <input placeholder="Channel" name="channel_name" value="${name}">
        <select name="channel_type">
          <option ${type==='text'?'selected':''}>text</option>
          <option ${type==='voice'?'selected':''}>voice</option>
          <option ${type==='forum'?'selected':''}>forum</option>
        </select>
        <input placeholder="Parent Category" name="channel_category" value="${category}">
        <label class="pill"><input type="checkbox" class="chan_rename"> rename</label>
        <input class="chan_rename_to renamebox" placeholder="New name" style="display:none" value="${renameTo}">
        <label class="pill danger"><input type="checkbox" class="chan_delete"> delete</label>
        <button type="button" onclick="this.parentElement.remove()">x</button>`;
      const cb = d.querySelector('.chan_rename');
      const to = d.querySelector('.chan_rename_to');
      cb.addEventListener('change', ()=>{ to.style.display = cb.checked ? 'inline-block' : 'none'; });
      if (renameTo) { cb.checked = true; to.style.display='inline-block'; }
      if (del) d.classList.add('delmark');
      return d;
    }

    // Adders
    function addRole(){ document.getElementById('roles').appendChild(roleRow()); }
    function addCat(){ document.getElementById('cats').appendChild(catRow()); refreshReassignOptions(); }
    function addChan(){ document.getElementById('chans').appendChild(chanRow()); }

    // Hydration helpers
    function clearSection(id){ const el = document.getElementById(id); while (el.firstChild) el.removeChild(el.firstChild); }

    function addRoleRow(name="", color="#000000"){ document.getElementById('roles').appendChild(roleRow(name, color)); }
    function addCatRow(name=""){ document.getElementById('cats').appendChild(catRow(name)); refreshReassignOptions(); }
    function addChanRow(name="", type="text", category=""){ document.getElementById('chans').appendChild(chanRow(name, type, category)); }

    function hydrateForm(p){
      clearSection('roles'); clearSection('cats'); clearSection('chans');

      const mode = (p.mode || 'build');
      const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
      if (radio) radio.checked = true;

      (p.roles || []).forEach(r => addRoleRow(r.name || "", r.color || "#000000"));
      if ((p.roles || []).length === 0) addRoleRow();

      (p.categories || []).forEach(c => addCatRow(c));
      if ((p.categories || []).length === 0) addCatRow();

      (p.channels || []).forEach(ch => addChanRow(ch.name || "", (ch.type||'text'), ch.category || ""));
      if ((p.channels || []).length === 0) addChanRow();
    }

    // Build the category reassign dropdowns from current non-deleted category names
    function refreshReassignOptions(){
      const cats = Array.from(document.querySelectorAll('#cats input[name="category"]')).map(i => (i.value||"").trim()).filter(Boolean);
      const rows = document.querySelectorAll('#cats .row');
      rows.forEach((row) => {
        const name = row.querySelector('input[name="category"]').value.trim();
        const sel = row.querySelector('.cat_reassign');
        if (!sel) return;
        const current = sel.value;
        sel.innerHTML = `<option value="">(no category)</option>` + cats.filter(c => c !== name).map(c => `<option value="${c}">${c}</option>`).join('');
        // try to keep selection if still valid
        if (current && current !== name && cats.includes(current)) sel.value = current;
      });
    }

    // Loaders
    async function loadLatest(){
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) return alert('Enter Guild ID');
      const res = await fetch(`/api/layout/${gid}/latest`);
      const data = await res.json();
      if (!data.ok) return alert(data.error || 'No layout');
      hydrateForm(data.payload || {});
      alert(`Loaded version ${data.version} from DB`);
    }

    async function loadLive(){
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) return alert('Enter Guild ID');
      const res = await fetch(`/api/live_layout/${gid}`);
      const data = await res.json();
      if (!data.ok) return alert(data.error || 'Failed to load live server');
      hydrateForm(data.payload || {});
      alert('Loaded from live server');
    }

    document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
    document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

    // ---------- Save ----------
    async function saveLayout(){
      const form = document.getElementById('layoutForm');
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) return alert('Enter Guild ID');

      const mode = form.mode.value;

      // collect roles
      const roles = [];
      const roleRenames = [];
      const roleDeletes = [];
      document.querySelectorAll('#roles .row').forEach(row => {
        const name = row.querySelector('input[name="role_name"]').value.trim();
        const color = row.querySelector('input[name="role_color"]').value;
        if (!name) return;
        roles.push({ name, color });
        const doRename = row.querySelector('.role_rename').checked;
        const to = row.querySelector('.role_rename_to').value.trim();
        if (doRename && to && to !== name) roleRenames.push({ from: name, to });
        if (row.querySelector('.role_delete').checked) roleDeletes.push(name);
      });

      // collect categories
      const categories = [];
      const catRenames = [];
      const catDeletes = [];
      document.querySelectorAll('#cats .row').forEach(row => {
        const name = row.querySelector('input[name="category"]').value.trim();
        if (!name) return;
        categories.push(name);
        const doRename = row.querySelector('.cat_rename').checked;
        const to = row.querySelector('.cat_rename_to').value.trim();
        if (doRename && to && to !== name) catRenames.push({ from: name, to });
        const del = row.querySelector('.cat_delete').checked;
        const reassign_to = row.querySelector('.cat_reassign').value.trim();
        if (del) catDeletes.push({ name, reassign_to });
      });

      // collect channels
      const channels = [];
      const chanRenames = [];
      const chanDeletes = [];
      const chanRows = document.querySelectorAll('#chans .row');
      chanRows.forEach(row => {
        const name = row.querySelector('input[name="channel_name"]').value.trim();
        let type = row.querySelector('select[name="channel_type"]').value.trim().toLowerCase();
        if (!['text','voice','forum'].includes(type)) type='text';
        const category = row.querySelector('input[name="channel_category"]').value.trim();
        if (!name) return;
        channels.push({ name, type, category });
        const doRename = row.querySelector('.chan_rename').checked;
        const to = row.querySelector('.chan_rename_to').value.trim();
        if (doRename && to && to !== name) chanRenames.push({ from: name, to });
        if (row.querySelector('.chan_delete').checked) chanDeletes.push({ name, type, category });
      });

      // SAFEGUARD: If a category marked for delete still has any listed channels, require a reassign target
      const channelsByCat = {};
      channels.forEach(ch => {
        const key = (ch.category || "").trim();
        if (!channelsByCat[key]) channelsByCat[key] = 0;
        channelsByCat[key] += 1;
      });
      for (const del of catDeletes) {
        const n = del.name;
        const count = channelsByCat[n] || 0;
        if (count > 0 && !('reassign_to' in del && del.reassign_to !== undefined)) {
          return alert(`Category "${n}" has ${count} channel(s). Choose a "reassign to" category, or uncheck delete.`);
        }
        if (count > 0 && del.reassign_to === n) {
          return alert(`Category "${n}" cannot reassign to itself. Pick a different category or uncheck delete.`);
        }
      }

      // Build payload (keep existing schema + explicit deletions)
      const payload = {
        mode,
        roles, categories, channels,
        renames: {
          roles: roleRenames,
          categories: catRenames,
          channels: chanRenames
        },
        deletions: {
          roles: roleDeletes,
          categories: catDeletes, // [{name, reassign_to}]
          channels: chanDeletes   // [{name,type,category}]
        }
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
