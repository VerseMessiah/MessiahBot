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

# Use latest bot snapshot saved by /snapshot_layout
@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id=%s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No snapshot found. Run /snapshot_layout in Discord first."}), 404
    return jsonify({"ok": True, "source": "snapshot", "version": int(row["version"]), "payload": row["payload"]})

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    incoming = request.get_json(silent=True) or {}
    if not incoming:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    latest = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id=%s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if latest and _json_equal(incoming, latest["payload"]):
        return jsonify({"ok": True, "version": int(latest["version"]), "no_change": True})

    row = _db_one(
        "SELECT COALESCE(MAX(version),0)+1 AS v FROM builder_layouts WHERE guild_id=%s",
        (guild_id,),
    )
    version = int((row or {}).get("v", 1))

    _db_exec(
        "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s,%s,%s::jsonb)",
        (guild_id, version, json.dumps(incoming)),
    )
    return jsonify({"ok": True, "version": version, "no_change": False})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id: str):
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id=%s ORDER BY version DESC LIMIT 1",
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
  <title>MessiahBot — Server Builder</title>
  <style>
    body{font-family:sans-serif;max-width:1000px;margin:24px auto;padding:0 12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px}
    button{margin:6px 6px 6px 0}
    .row{display:flex;gap:8px;align-items:center}
    .stack{display:flex;flex-direction:column;gap:8px}
    .list{display:flex;flex-direction:column;gap:8px; padding:8px; border:1px solid #ddd; border-radius:8px; background:#fafafa}
    .cat{border:1px solid #ccc;border-radius:10px;padding:12px;background:#fff}
    .cat-head{display:flex;gap:8px;align-items:center;margin-bottom:8px}
    .pill{font-size:12px;padding:2px 6px;border:1px solid #aaa;border-radius:999px;background:#f3f3f3}
    .muted{color:#555}
    .w200{width:200px}
    .w260{width:260px}
    .w120{width:120px}
    .w80{width:80px}
    .role-item, .chan-item {background:#fff;border:1px solid #ddd;border-radius:8px;padding:8px}
    .section-title{display:flex;justify-content:space-between;align-items:center}
  </style>
  <!-- SortableJS for nice drag & drop -->
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>
</head>
<body>
  <h1>🧱 MessiahBot — Server Builder</h1>
  <p class="muted">Workflow: run <code>/snapshot_layout</code> in Discord → enter Guild ID → <strong>Load From Bot Snapshot</strong>. Drag to reorder; channels inherit parent by nesting.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <div class="row">
    <label>Guild ID <input class="w260" type="text" id="guild_id" required></label>
    <button type="button" id="loadLiveBtn">Load From Bot Snapshot</button>
    <button type="button" id="loadLatestBtn">Load Latest From DB</button>
  </div>

  <fieldset>
    <legend>Mode</legend>
    <label><input type="radio" name="mode" value="build" checked> Build</label>
    <label><input type="radio" name="mode" value="update"> Update</label>
  </fieldset>

  <div class="section-title">
    <h3>Roles</h3>
    <button type="button" onclick="addRole()">Add Role</button>
  </div>
  <div id="roles" class="list"></div>

  <div class="section-title">
    <h3>Categories & Channels</h3>
    <button type="button" onclick="addCategory()">Add Category</button>
  </div>
  <div id="cats" class="stack"></div>

  <fieldset>
    <legend>Danger Zone</legend>
    <p class="muted">These options can delete or rename live items. Use carefully.</p>
    <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
    <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
    <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>

    <h4>Renames</h4>
    <div class="stack">
      <div>
        <span class="pill">Roles</span>
        <div id="roleRenames" class="list"></div>
        <button type="button" onclick="addRename('roleRenames')">Add Role Rename</button>
      </div>
      <div>
        <span class="pill">Categories</span>
        <div id="catRenames" class="list"></div>
        <button type="button" onclick="addRename('catRenames')">Add Category Rename</button>
      </div>
      <div>
        <span class="pill">Channels</span>
        <div id="chanRenames" class="list"></div>
        <button type="button" onclick="addRename('chanRenames')">Add Channel Rename</button>
      </div>
    </div>
  </fieldset>

  <p>
    <button type="button" id="saveBtn">Save Layout</button>
  </p>

<script>
  // ---------- Drag helpers ----------
  function makeSortable(el, opts={}){ return new Sortable(el, Object.assign({
    animation: 150,
    ghostClass: 'sortable-ghost',
    handle: '.drag',
  }, opts)); }

  // ---------- Roles UI ----------
  function roleRow(name="", color="#000000"){
    const d = document.createElement('div');
    d.className = "role-item row";
    d.innerHTML = `
      <span class="drag">↕</span>
      <input class="w260" placeholder="Role name" name="role_name" value="${name}">
      <input type="color" name="role_color" value="${color || '#000000'}">
      <button type="button" onclick="this.parentElement.remove()">x</button>
    `;
    return d;
  }
  function addRole(){ document.getElementById('roles').appendChild(roleRow()); }

  // ---------- Channels UI ----------
  function chanRow(name="", type="text"){
    const d = document.createElement('div');
    d.className = "chan-item row";
    d.innerHTML = `
      <span class="drag">↕</span>
      <input class="w260" placeholder="Channel name" name="channel_name" value="${name}">
      <select name="channel_type" class="w120">
        <option ${type==='text'?'selected':''} value="text">text</option>
        <option ${type==='announcement'?'selected':''} value="announcement">announcement</option>
        <option ${type==='voice'?'selected':''} value="voice">voice</option>
        <option ${type==='forum'?'selected':''} value="forum">forum</option>
      </select>
      <input class="w260" placeholder="Topic/Description (text only)" name="channel_topic">
      <label class="pill"><input type="checkbox" name="channel_nsfw"> NSFW</label>
      <input class="w80" type="number" min="0" step="1" value="0" name="channel_slowmode" title="Slowmode (sec)">
      <button type="button" onclick="this.parentElement.remove()">x</button>
    `;
    return d;
  }

  // ---------- Categories UI (with nested channel list) ----------
  function categoryBlock(name=""){
    const wrapper = document.createElement('div');
    wrapper.className = "cat";
    wrapper.innerHTML = `
      <div class="cat-head">
        <span class="drag">↕</span>
        <input class="w260" placeholder="Category name" name="category_name" value="${name}">
        <button type="button" onclick="this.closest('.cat').remove()">x</button>
      </div>
      <div class="list chan-list"></div>
      <button type="button" onclick="this.previousElementSibling.appendChild(chanRow());">Add Channel</button>
    `;
    // make its channel list sortable and cross-category draggable
    const chanList = wrapper.querySelector('.chan-list');
    makeSortable(chanList, { group: 'channels' });
    return wrapper;
  }
  function addCategory(){ document.getElementById('cats').appendChild(categoryBlock()); }

  // Make Roles & Categories containers sortable
  document.addEventListener('DOMContentLoaded', () => {
    makeSortable(document.getElementById('roles'));
    makeSortable(document.getElementById('cats'));
  });

  // ---------- Renames ----------
  function renameRow(fromVal="", toVal=""){
    const d = document.createElement('div');
    d.className = "row";
    d.innerHTML = `
      <input class="w200" placeholder="From name" value="${fromVal}" data-rename-from>
      <span>→</span>
      <input class="w200" placeholder="To name" value="${toVal}" data-rename-to>
      <button type="button" onclick="this.parentElement.remove()">x</button>
    `;
    return d;
  }
  function addRename(containerId){ document.getElementById(containerId).appendChild(renameRow()); }
  function collectRenames(containerId){
    const out = [];
    const rows = document.querySelectorAll(`#${containerId} [data-rename-from]`);
    rows.forEach((fromEl, i) => {
      const toEl = document.querySelectorAll(`#${containerId} [data-rename-to]`)[i];
      const from = (fromEl.value||"").trim(), to = (toEl.value||"").trim();
      if (from && to) out.push({from,to});
    });
    return out;
  }

  // ---------- Hydrate ----------
  function hydrate(layout){
    // mode
    const mode = (layout.mode || 'update');
    const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    // danger toggles
    document.getElementById('prune_roles').checked = !!(layout.prune && layout.prune.roles);
    document.getElementById('prune_categories').checked = !!(layout.prune && layout.prune.categories);
    document.getElementById('prune_channels').checked = !!(layout.prune && layout.prune.channels);

    // renames
    ['roleRenames','catRenames','chanRenames'].forEach(id => {
      const el = document.getElementById(id);
      while (el.firstChild) el.removeChild(el.firstChild);
    });
    (layout.renames?.roles || []).forEach(x => document.getElementById('roleRenames').appendChild(renameRow(x.from||"", x.to||"")));
    (layout.renames?.categories || []).forEach(x => document.getElementById('catRenames').appendChild(renameRow(x.from||"", x.to||"")));
    (layout.renames?.channels || []).forEach(x => document.getElementById('chanRenames').appendChild(renameRow(x.from||"", x.to||"")));

    // roles
    const rolesEl = document.getElementById('roles');
    rolesEl.innerHTML = "";
    (layout.roles || []).forEach(r => rolesEl.appendChild(roleRow(r.name||"", r.color||"#000000")));
    if ((layout.roles || []).length === 0) rolesEl.appendChild(roleRow());

    // categories + channels (nested)
    const catsEl = document.getElementById('cats');
    catsEl.innerHTML = "";

    // normalize categories array to simple list of names
    const catNames = (layout.categories || []).map(c => (typeof c === 'string') ? c : (c?.name || ""));

    // group channels by category (name)
    const byCat = {};
    (layout.channels || []).forEach(ch => {
      const cat = (ch.category || "").trim();
      byCat[cat] = byCat[cat] || [];
      byCat[cat].push(ch);
    });

    // build blocks in the order of catNames, then any channels with no/unknown category at the end in a blank category
    catNames.forEach(cn => {
      const block = categoryBlock(cn || "");
      const list = block.querySelector('.chan-list');
      (byCat[cn] || []).forEach(ch => {
        const row = chanRow(ch.name||"", (ch.type||"text"));
        // hydrate options if present
        row.querySelector('input[name="channel_topic"]').value = (ch.options?.topic || "");
        row.querySelector('input[name="channel_nsfw"]').checked = !!(ch.options?.nsfw || ch.options?.age_restricted);
        row.querySelector('input[name="channel_slowmode"]').value = parseInt(ch.options?.slowmode || 0);
        list.appendChild(row);
      });
      catsEl.appendChild(block);
    });

    // orphan channels (no category or category not in list)
    const orphans = Object.keys(byCat).filter(k => !catNames.includes(k) && (k||"") !== "");
    if ((byCat[""] || []).length || orphans.length){
      const block = categoryBlock("");
      const list = block.querySelector('.chan-list');
      (byCat[""] || []).forEach(ch => {
        const row = chanRow(ch.name||"", (ch.type||"text"));
        row.querySelector('input[name="channel_topic"]').value = (ch.options?.topic || "");
        row.querySelector('input[name="channel_nsfw"]').checked = !!(ch.options?.nsfw || ch.options?.age_restricted);
        row.querySelector('input[name="channel_slowmode"]').value = parseInt(ch.options?.slowmode || 0);
        list.appendChild(row);
      });
      orphans.forEach(cat => {
        (byCat[cat] || []).forEach(ch => {
          const row = chanRow(ch.name||"", (ch.type||"text"));
          row.querySelector('input[name="channel_topic"]').value = (ch.options?.topic || "");
          row.querySelector('input[name="channel_nsfw"]').checked = !!(ch.options?.nsfw || ch.options?.age_restricted);
          row.querySelector('input[name="channel_slowmode"]').value = parseInt(ch.options?.slowmode || 0);
          list.appendChild(row);
        });
      });
      catsEl.appendChild(block);
    }
  }

  // ---------- Loaders ----------
  async function loadLatest() {
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');
    const res = await fetch(`/api/layout/${gid}/latest`);
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'No layout');
    hydrate(data.payload || {});
    alert(`Loaded version ${data.version} from DB`);
  }
  async function loadLive() {
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');
    const res = await fetch(`/api/live_layout/${gid}`);
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'No snapshot found. Run /snapshot_layout in Discord, then retry.');
    hydrate(data.payload || {});
    alert(`Loaded from bot snapshot (version ${data.version || 'n/a'})`);
  }
  document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
  document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

  // ---------- Saver ----------
  async function saveLayout(){
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');

    // mode
    const mode = document.querySelector('input[name="mode"]:checked')?.value || 'update';

    // roles in visible order
    const roles = [];
    document.querySelectorAll('#roles .role-item').forEach(r => {
      const name = r.querySelector('input[name="role_name"]').value.trim();
      const color = r.querySelector('input[name="role_color"]').value || '#000000';
      if (name) roles.push({ name, color });
    });

    // categories in visible order, with nested channels (parent is implicit)
    const categories = [];
    const channels = [];
    document.querySelectorAll('#cats .cat').forEach(cat => {
      const cname = (cat.querySelector('input[name="category_name"]').value || "").trim();
      categories.push(cname);
      const list = cat.querySelector('.chan-list');
      list.querySelectorAll('.chan-item').forEach(ch => {
        const nm = ch.querySelector('input[name="channel_name"]').value.trim();
        const tp = ch.querySelector('select[name="channel_type"]').value;
        const topic = ch.querySelector('input[name="channel_topic"]').value;
        const nsfw = ch.querySelector('input[name="channel_nsfw"]').checked;
        const slow = parseInt(ch.querySelector('input[name="channel_slowmode"]').value || "0", 10) || 0;
        if (nm){
          channels.push({
            name: nm,
            type: tp,
            category: cname,
            options: { topic, nsfw, slowmode: slow }
          });
        }
      });
    });

    // danger toggles
    const prune = {
      roles: document.getElementById('prune_roles').checked,
      categories: document.getElementById('prune_categories').checked,
      channels: document.getElementById('prune_channels').checked,
    };

    // renames
    const renames = {
      roles: collectRenames('roleRenames'),
      categories: collectRenames('catRenames'),
      channels: collectRenames('chanRenames'),
    };

    const payload = { mode, roles, categories, channels, prune, renames };

    const res = await fetch(`/api/layout/${gid}`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok && data.no_change) alert(`No changes detected. Current version is still ${data.version}.`);
    else if (data.ok) alert(`Saved version ${data.version}`);
    else alert(data.error || 'Error');
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
