# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # needed to read live server via REST

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
    Includes:
      - roles: name, color, permissions (bitfield)
      - categories: name, position, overwrites
      - channels: name, type, category, position, topic, overwrites
    """
    try:
        import requests
    except Exception:
        return jsonify({"ok": False, "error": "Python 'requests' not installed; add requests to requirements.txt"}), 500

    if not DISCORD_BOT_TOKEN:
        return jsonify({"ok": False, "error": "DISCORD_BOT_TOKEN not set on web service"}), 500

    base = "https://discord.com/api/v10"
    headers = _discord_headers()

    # 1) roles
    r_roles = requests.get(f"{base}/guilds/{guild_id}/roles", headers=headers, timeout=20)
    if r_roles.status_code == 403:
        return jsonify({"ok": False, "error": "Forbidden: bot lacks permission or is not in this guild"}), 403
    if r_roles.status_code == 404:
        return jsonify({"ok": False, "error": "Guild not found (check Guild ID)"}), 404
    if r_roles.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord roles error {r_roles.status_code}: {r_roles.text}"}), 502
    roles_json = r_roles.json()  # list of role objects

    role_id_to_name = {}
    roles = []
    for r in roles_json:
        if r.get("managed"):
            continue
        if r.get("name") == "@everyone":
            # @everyone can have permissions but we skip it for layout roles
            continue
        color_int = r.get("color") or 0
        perm_int = int(r.get("permissions", "0"))
        role_id_to_name[str(r.get("id"))] = r.get("name", "")
        roles.append({
            "name": r.get("name", ""),
            "color": f"#{color_int:06x}",
            "permissions": perm_int
        })

    # 2) channels
    r_channels = requests.get(f"{base}/guilds/{guild_id}/channels", headers=headers, timeout=20)
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord channels error {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    # Build category list + map id->(name, position, overwrites)
    categories = []
    cat_map = {}
    for c in channels_json:
        if c.get("type") == 4:  # category
            name = c.get("name", "")
            pos = c.get("position", 0)
            ows = []
            for ow in c.get("permission_overwrites", []) or []:
                # ow: {id, type:0 role|1 member, allow, deny}
                t = "role" if ow.get("type") == 0 else "member"
                allow = int(ow.get("allow", "0"))
                deny  = int(ow.get("deny", "0"))
                entry = {"type": t, "id": str(ow.get("id")), "allow": allow, "deny": deny}
                # decorate name for roles where possible
                if t == "role":
                    entry["name"] = role_id_to_name.get(str(ow.get("id")), "")
                ows.append(entry)
            categories.append({"name": name, "position": pos, "overwrites": ows})
            cat_map[c["id"]] = {"name": name, "position": pos}

    # Channels: include type, parent category name, position, topic, overwrites
    chans = []
    # sort by position (Discord returns positions)
    channels_sorted = sorted(channels_json, key=lambda x: x.get("position", 0))
    for ch in channels_sorted:
        t = ch.get("type")
        if t == 4:
            continue  # already handled as categories
        name = ch.get("name", "")
        parent_id = ch.get("parent_id")
        parent_name = cat_map.get(parent_id, {}).get("name", "") if parent_id else ""
        pos = ch.get("position", 0)
        # topics: for text/forum; voice has none
        topic = ch.get("topic", "") if t in (0, 15) else ""

        ows = []
        for ow in ch.get("permission_overwrites", []) or []:
            typ = "role" if ow.get("type") == 0 else "member"
            allow = int(ow.get("allow", "0"))
            deny = int(ow.get("deny", "0"))
            entry = {"type": typ, "id": str(ow.get("id")), "allow": allow, "deny": deny}
            if typ == "role":
                entry["name"] = role_id_to_name.get(str(ow.get("id")), "")
            ows.append(entry)

        if t == 0:   # text
            chans.append({"name": name, "type": "text", "category": parent_name, "position": pos, "topic": topic, "overwrites": ows})
        elif t == 2: # voice
            chans.append({"name": name, "type": "voice", "category": parent_name, "position": pos, "topic": "", "overwrites": ows})
        elif t == 15:  # forum
            chans.append({"name": name, "type": "forum", "category": parent_name, "position": pos, "topic": topic, "overwrites": ows})
        else:
            # other types ignored
            pass

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
  <title>MessiahBot — Server Layout</title>
  <style>
    body{font-family:sans-serif;max-width:1100px;margin:24px auto;padding:0 12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px;border:1px solid #ddd}
    button{margin-top:6px}
    .row{display:flex;gap:8px;align-items:center;margin:6px 0}
    .row input[type="text"]{min-width:180px}
    .row textarea{min-width:320px;min-height:60px}
    .mini{width:70px}
    .topic{min-width:280px}
    .section{border:1px solid #eee;padding:10px;border-radius:8px;margin:10px 0}
    .muted{color:#666}
    .controls button{margin-left:4px}
    .pill{display:inline-block;padding:2px 8px;background:#eef;border:1px solid #99c;border-radius:999px;font-size:12px}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Server Layout</h1>
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

    <div class="section">
      <h3>Roles <span class="pill">name, color, permissions</span></h3>
      <div id="roles"></div>
      <button type="button" onclick="addRole()">Add Role</button>
      <div class="muted">Permissions is a bitfield (integer). You can leave 0 to not change perms.</div>
    </div>

    <div class="section">
      <h3>Categories <span class="pill">name, position, overwrites JSON</span></h3>
      <div id="cats"></div>
      <button type="button" onclick="addCat()">Add Category</button>
      <div class="muted">Overwrites JSON = <code>[{ "type":"role|member","id":"123","allow":0,"deny":0,"name":"Role (optional)"}, ...]</code></div>
    </div>

    <div class="section">
      <h3>Channels <span class="pill">name, type, category, position, topic, overwrites JSON</span></h3>
      <div id="chans"></div>
      <button type="button" onclick="addChan()">Add Channel</button>
    </div>

    <div class="section">
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
    </div>

    <p><button type="button" id="saveBtn">Save Layout</button></p>
  </form>

  <script>
    // Prevent Enter from auto-submitting the form
    document.getElementById('layoutForm').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
        e.preventDefault();
      }
    });

    // --- Utilities ---
    function moveUp(btn){ const row = btn.closest('.row'); if(row && row.previousElementSibling){ row.parentElement.insertBefore(row, row.previousElementSibling); } }
    function moveDown(btn){ const row = btn.closest('.row'); if(row && row.nextElementSibling){ row.parentElement.insertBefore(row.nextElementSibling, row); } }

    function addRole(name="", color="#000000", perms="0"){
      const d=document.createElement('div');
      d.className="row";
      d.innerHTML=`<input placeholder="Role" name="role_name" value="${name}">
                   <input type="color" name="role_color" value="${color||'#000000'}">
                   <input class="mini" type="text" name="role_perms" value="${perms||'0'}" title="permissions bitfield">
                   <span class="controls">
                     <button type="button" onclick="moveUp(this)">↑</button>
                     <button type="button" onclick="moveDown(this)">↓</button>
                     <button type="button" onclick="this.closest('.row').remove()">x</button>
                   </span>`;
      document.getElementById('roles').appendChild(d);
    }

    function addCat(name="", position="", overwrites=""){
      const d=document.createElement('div');
      d.className="row";
      const owStr = overwrites ? JSON.stringify(overwrites) : "";
      d.innerHTML=`<input placeholder="Category" name="category" value="${name}">
                   <input class="mini" placeholder="pos" name="category_position" value="${position!==undefined? position : ''}">
                   <textarea name="category_overwrites" placeholder='[{"type":"role","id":"...","allow":0,"deny":0}]'>${owStr}</textarea>
                   <span class="controls">
                     <button type="button" onclick="moveUp(this)">↑</button>
                     <button type="button" onclick="moveDown(this)">↓</button>
                     <button type="button" onclick="this.closest('.row').remove()">x</button>
                   </span>`;
      document.getElementById('cats').appendChild(d);
    }

    function addChan(name="", type="text", category="", position="", topic="", overwrites=""){
      const d=document.createElement('div');
      d.className="row";
      const owStr = overwrites ? JSON.stringify(overwrites) : "";
      d.innerHTML=`<input placeholder="Channel" name="channel_name" value="${name}">
                   <select name="channel_type">
                     <option ${type==='text'?'selected':''}>text</option>
                     <option ${type==='voice'?'selected':''}>voice</option>
                     <option ${type==='forum'?'selected':''}>forum</option>
                   </select>
                   <input placeholder="Parent Category" name="channel_category" value="${category}">
                   <input class="mini" placeholder="pos" name="channel_position" value="${position!==undefined? position : ''}">
                   <input class="topic" placeholder="Topic/Description" name="channel_topic" value="${(topic||'').replace(/"/g,'&quot;')}">
                   <textarea name="channel_overwrites" placeholder='[{"type":"role","id":"...","allow":0,"deny":0}]'>${owStr}</textarea>
                   <span class="controls">
                     <button type="button" onclick="moveUp(this)">↑</button>
                     <button type="button" onclick="moveDown(this)">↓</button>
                     <button type="button" onclick="this.closest('.row').remove()">x</button>
                   </span>`;
      document.getElementById('chans').appendChild(d);
    }

    function addRename(containerId, fromVal="", toVal=""){
      const d = document.createElement('div');
      d.className="row";
      d.innerHTML = `<input placeholder="From name" class="rename_from" value="${fromVal}">
                     <span>→</span>
                     <input placeholder="To name" class="rename_to" value="${toVal}">
                     <button type="button" onclick="this.closest('.row').remove()">x</button>`;
      document.getElementById(containerId).appendChild(d);
    }

    function collectRenames(containerId){
      const out = [];
      const root = document.getElementById(containerId);
      if (!root) return out;
      const fromEls = root.querySelectorAll('.rename_from');
      const toEls   = root.querySelectorAll('.rename_to');
      for (let i=0;i<fromEls.length;i++){
        const from = (fromEls[i].value||"").trim();
        const to   = (toEls[i].value||"").trim();
        if (from && to) out.push({from, to});
      }
      return out;
    }

    function clearSection(id) {
      const el = document.getElementById(id);
      while (el.firstChild) el.removeChild(el.firstChild);
    }

    function hydrateForm(p) {
      // Reset danger-zone
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
      (p.roles || []).forEach(r => addRole(r.name || "", r.color || "#000000", (r.permissions ?? 0)));
      if ((p.roles || []).length === 0) addRole();

      clearSection('cats');
      (p.categories || []).forEach(c => addCat(c.name || "", (c.position ?? ""), (c.overwrites || "")));
      if ((p.categories || []).length === 0) addCat();

      clearSection('chans');
      (p.channels || []).forEach(ch => addChan(
        ch.name || "",
        (ch.type || 'text'),
        (ch.category || ""),
        (ch.position ?? ""),
        (ch.topic || ""),
        (ch.overwrites || "")
      ));
      if ((p.channels || []).length === 0) addChan();
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

    document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
    document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

    // Save layout
    async function saveLayout(){
      const form = document.getElementById('layoutForm');
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }

      const roles = [], categories = [], channels = [];
      const mode = form.mode.value;

      // ROLES in DOM order
      document.querySelectorAll('#roles .row').forEach(row => {
        const name = row.querySelector('input[name="role_name"]').value.trim();
        const color = row.querySelector('input[name="role_color"]').value || "#000000";
        const perms = row.querySelector('input[name="role_perms"]').value.trim();
        if (name){
          roles.push({
            name, color,
            permissions: (perms === "" ? 0 : Number(perms)||0)
          });
        }
      });

      // CATEGORIES in DOM order; position = index if not provided
      document.querySelectorAll('#cats .row').forEach((row, idx) => {
        const name = row.querySelector('input[name="category"]').value.trim();
        const posStr = row.querySelector('input[name="category_position"]').value.trim();
        const owStr  = row.querySelector('textarea[name="category_overwrites"]').value.trim();
        if (name){
          let overwrites = [];
          if (owStr){
            try{ overwrites = JSON.parse(owStr); } catch(e){ alert('Bad category overwrites JSON for '+name); throw e; }
          }
          let position = posStr !== "" ? Number(posStr) : idx;
          categories.push({ name, position, overwrites });
        }
      });

      // CHANNELS in DOM order; position = index within list if not provided
      document.querySelectorAll('#chans .row').forEach((row, idx) => {
        const name = row.querySelector('input[name="channel_name"]').value.trim();
        const type = row.querySelector('select[name="channel_type"]').value;
        const category = row.querySelector('input[name="channel_category"]').value.trim();
        const posStr = row.querySelector('input[name="channel_position"]').value.trim();
        const topic = row.querySelector('input[name="channel_topic"]').value;
        const owStr = row.querySelector('textarea[name="channel_overwrites"]').value.trim();
        if (name){
          let overwrites = [];
          if (owStr){
            try{ overwrites = JSON.parse(owStr); } catch(e){ alert('Bad channel overwrites JSON for '+name); throw e; }
          }
          let position = posStr !== "" ? Number(posStr) : idx;
          channels.push({ name, type, category, position, topic, overwrites });
        }
      });

      // Danger zone flags
      const prune_roles = document.getElementById('prune_roles').checked;
      const prune_categories = document.getElementById('prune_categories').checked;
      const prune_channels = document.getElementById('prune_channels').checked;

      // Renames
      const roles_rename = collectRenames('roleRenames');
      const categories_rename = collectRenames('catRenames');
      const channels_rename = collectRenames('chanRenames');

      const payload = {
        mode,
        roles, categories, channels,
        prune: {
          roles: prune_roles,
          categories: prune_categories,
          channels: prune_channels
        },
        renames: {
          roles: roles_rename,
          categories: categories_rename,
          channels: channels_rename
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

# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    # Local dev runner. On Render, you can also use:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
