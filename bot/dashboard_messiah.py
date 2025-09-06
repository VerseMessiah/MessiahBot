import os, json, time
from flask import Flask, request, jsonify, render_template_string

DATABASE_URL = os.getenv("DATABASE_URL")
_psycopg_ok = False
try:
    import psycopg
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

app = Flask(__name__)

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

# ---------- health ----------
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
    }
    code = 200 if (ok_env and ok_driver and ok_connect) else 500
    return status, code

# ---------- data APIs ----------
@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    Loads the latest snapshot saved by /snapshot_layout (bot side).
    No Discord REST (avoids Cloudflare 1015).
    """
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

# ---------- UI ----------
_VER = str(int(time.time()))  # cache-bust on each deploy

_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Server Builder</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      --bg: #0b0d10;
      --panel: #12151a;
      --muted: #aab0bb;
      --text: #e7ecf3;
      --border: #232832;
      --accent: #7dd3fc;
      --danger: #fca5a5;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg: #fafafa;
        --panel: #ffffff;
        --muted: #475569;
        --text: #0f172a;
        --border: #e5e7eb;
        --accent: #0369a1;
        --danger: #b91c1c;
      }
    }
    body { background: var(--bg); color: var(--text); font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin:0; }
    .container { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
    a { color: var(--accent); }
    .panel { background: var(--panel); border:1px solid var(--border); border-radius:12px; padding:16px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap: wrap; }
    .stack { display:flex; flex-direction:column; gap:12px; }
    .list { display:flex; flex-direction:column; gap:8px; padding:8px; border:1px dashed var(--border); border-radius:10px; background: color-mix(in oklab, var(--panel) 70%, black 30%); }
    .cat { border:1px solid var(--border); border-radius:12px; padding:12px; background: var(--panel); }
    .cat-head { display:flex; gap:8px; align-items:center; margin-bottom:8px }
    .pill { font-size:12px; padding:2px 6px; border:1px solid var(--border); border-radius:999px; color: var(--muted); }
    .muted { color: var(--muted); }
    .w200 { width:200px }
    .w260 { width:260px }
    .w140 { width:140px }
    .w80 { width:80px }
    .role-item, .chan-item { background: var(--panel); border:1px solid var(--border); border-radius:8px; padding:8px }
    .section-title { display:flex; justify-content:space-between; align-items:center; }
    button { background:#1f2937; color:#e5e7eb; border:1px solid var(--border); padding:8px 10px; border-radius:10px; cursor:pointer; }
    button:hover { filter: brightness(1.1); }
    .danger { color: var(--danger); }
    .drag { cursor: grab; user-select:none; }
    .ghost { opacity: .6; }
    .toggle { margin-left:auto }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>
  <script>
    // Expose cache-buster to JS without using Python f-strings in HTML
    window.__VER__ = "{{ ver }}";
  </script>
</head>
<body>
{% raw %}
<div class="container stack">
  <div class="row" style="justify-content:space-between;">
    <h1>🧱 MessiahBot — Server Builder</h1>
    <button id="themeBtn" class="toggle" type="button">🌓 Theme</button>
  </div>
  <div class="panel stack">
    <p class="muted">Flow: in Discord run <code>/snapshot_layout</code> → enter Guild ID → <b>Load From Bot Snapshot</b>. Drag to reorder. Inline rename by typing over names.</p>
    <div class="row">
      <label>Guild ID <input class="w260" type="text" id="guild_id" required></label>
      <button type="button" id="loadLiveBtn">Load From Bot Snapshot</button>
      <button type="button" id="loadLatestBtn">Load Latest From DB</button>
      <span class="muted"><a href="/dbcheck" target="_blank">dbcheck</a> • <a href="/routes" target="_blank">routes</a></span>
    </div>
  </div>

  <div class="panel stack">
    <fieldset style="border:0; padding:0; margin:0">
      <legend class="pill">Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <div class="section-title">
      <h3>Roles</h3>
      <button type="button" onclick="addRole()">Add Role</button>
    </div>
    <div id="roles" class="list"></div>

    <div class="section-title" style="margin-top:12px">
      <h3>Categories & Channels</h3>
      <button type="button" onclick="addCategory()">Add Category</button>
    </div>
    <div id="cats" class="stack"></div>
  </div>

  <div class="panel stack">
    <fieldset style="border:0; padding:0; margin:0">
      <legend class="pill danger">Danger Zone</legend>
      <p class="muted">These options can delete or rename live items. Use carefully.</p>
      <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
      <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
      <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>
    </fieldset>

    <h4>Renames (optional)</h4>
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
  </div>

  <div class="panel">
    <button type="button" id="saveBtn">💾 Save Layout</button>
  </div>
</div>

<script>
  // ---------- Theme toggle ----------
  (function(){
    const btn = document.getElementById('themeBtn');
    btn.addEventListener('click', () => {
      document.documentElement.classList.toggle('force-light');
      document.documentElement.classList.toggle('force-dark');
    });
  })();

  // Track original snapshot for auto-rename detection
  let ORIGINAL = {}; // filled on hydrate

  // ---------- Drag helpers ----------
  const Sortables = new Set();
  function makeSortable(el, opts={}){
    if (!el) return null;
    if (el.__sortable) return el.__sortable;
    const s = new Sortable(el, Object.assign({ animation: 150, ghostClass: 'ghost', handle: '.drag' }, opts));
    el.__sortable = s;
    Sortables.add(s);
    return s;
  }
  function rewireDragAndDrop(){
    makeSortable(document.getElementById('roles'));
    makeSortable(document.getElementById('cats'));
    document.querySelectorAll('.chan-list').forEach(list => {
      makeSortable(list, { group: 'channels', swapThreshold: 0.6 });
    });
  }

  // ---------- Role UI ----------
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

  // ---------- Channel UI ----------
  function chanRow(name="", type="text", topic="", nsfw=false, slowmode=0){
    const d = document.createElement('div');
    d.className = "chan-item row";
    d.innerHTML = `
      <span class="drag">↕</span>
      <input class="w260" placeholder="Channel name" name="channel_name" value="${name}">
      <select name="channel_type" class="w140">
        <option ${type==='text'?'selected':''} value="text">text</option>
        <option ${type==='announcement'?'selected':''} value="announcement">announcement</option>
        <option ${type==='voice'?'selected':''} value="voice">voice</option>
        <option ${type==='forum'?'selected':''} value="forum">forum</option>
      </select>
      <input class="w260" placeholder="Topic/Description (text only)" name="channel_topic" value="${topic||''}">
      <label class="pill"><input type="checkbox" name="channel_nsfw" ${nsfw?'checked':''}> NSFW</label>
      <input class="w80" type="number" min="0" step="1" value="${parseInt(slowmode||0)}" name="channel_slowmode" title="Slowmode (sec)">
      <button type="button" onclick="this.parentElement.remove()">x</button>
    `;
    return d;
  }

  // ---------- Category UI ----------
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
    makeSortable(wrapper.querySelector('.chan-list'), { group: 'channels', swapThreshold: 0.6 });
    return wrapper;
  }
  function addCategory(){ document.getElementById('cats').appendChild(categoryBlock()); }

  // ---------- Renames UI ----------
  function renameRow(fromVal="", toVal=""){
    const d = document.createElement('div'); d.className = "row";
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
    document.querySelectorAll(`#${containerId} [data-rename-from]`).forEach((fromEl, i) => {
      const toEl = document.querySelectorAll(`#${containerId} [data-rename-to]`)[i];
      const from = (fromEl.value||"").trim(), to = (toEl.value||"").trim();
      if (from && to) out.push({from,to});
    });
    return out;
  }

  // ---------- Hydrate ----------
  function hydrate(layout){
    ORIGINAL = JSON.parse(JSON.stringify(layout||{}));

    const mode = (layout.mode || 'update');
    const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (radio) radio.checked = true;

    document.getElementById('prune_roles').checked = !!(layout.prune && layout.prune.roles);
    document.getElementById('prune_categories').checked = !!(layout.prune && layout.prune.categories);
    document.getElementById('prune_channels').checked = !!(layout.prune && layout.prune.channels);

    ['roleRenames','catRenames','chanRenames'].forEach(id => {
      const el = document.getElementById(id); while (el.firstChild) el.removeChild(el.firstChild);
    });
    (layout.renames?.roles || []).forEach(x => document.getElementById('roleRenames').appendChild(renameRow(x.from||"", x.to||"")));
    (layout.renames?.categories || []).forEach(x => document.getElementById('catRenames').appendChild(renameRow(x.from||"", x.to||"")));
    (layout.renames?.channels || []).forEach(x => document.getElementById('chanRenames').appendChild(renameRow(x.from||"", x.to||"")));

    const rolesEl = document.getElementById('roles'); rolesEl.innerHTML = "";
    (layout.roles || []).forEach(r => rolesEl.appendChild(roleRow(r.name||"", r.color||"#000000")));
    if ((layout.roles || []).length === 0) rolesEl.appendChild(roleRow());

    const catsEl = document.getElementById('cats'); catsEl.innerHTML = "";
    const catNames = (layout.categories || []).map(c => (typeof c === 'string') ? c : (c?.name || ""));
    const byCat = {};
    (layout.channels || []).forEach(ch => {
      const cat = (ch.category || "").trim();
      (byCat[cat] = byCat[cat] || []).push(ch);
    });
    catNames.forEach(cn => {
      const block = categoryBlock(cn || "");
      const list = block.querySelector('.chan-list');
      (byCat[cn] || []).forEach(ch => {
        const topic = ch.options?.topic || "";
        const nsfw = !!(ch.options?.nsfw || ch.options?.age_restricted);
        const slow = parseInt(ch.options?.slowmode || 0) || 0;
        list.appendChild(chanRow(ch.name||"", (ch.type||"text"), topic, nsfw, slow));
      });
      catsEl.appendChild(block);
    });
    const unknownCats = Object.keys(byCat).filter(k => !catNames.includes(k));
    if ((byCat[""]||[]).length || unknownCats.length){
      const block = categoryBlock("");
      const list = block.querySelector('.chan-list');
      (byCat[""]||[]).forEach(ch => {
        list.appendChild(chanRow(ch.name||"", (ch.type||"text"), ch.options?.topic||"", !!(ch.options?.nsfw||ch.options?.age_restricted), parseInt(ch.options?.slowmode||0)||0));
      });
      unknownCats.forEach(cn => {
        (byCat[cn]||[]).forEach(ch => {
          list.appendChild(chanRow(ch.name||"", (ch.type||"text"), ch.options?.topic||"", !!(ch.options?.nsfw||ch.options?.age_restricted), parseInt(ch.options?.slowmode||0)||0));
        });
      });
      catsEl.appendChild(block);
    }

    rewireDragAndDrop();
  }

  // ---------- Loaders ----------
  async function loadLatest(){
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');
    const res = await fetch(`/api/layout/${gid}/latest?v=${window.__VER__}`);
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'No layout');
    hydrate(data.payload || {});
    alert(`Loaded version ${data.version} from DB`);
  }
  async function loadLive(){
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');
    const res = await fetch(`/api/live_layout/${gid}?v=${window.__VER__}`);
    const data = await res.json();
    if (!data.ok) return alert(data.error || 'No snapshot found. Run /snapshot_layout in Discord, then retry.');
    hydrate(data.payload || {});
    alert(`Loaded from bot snapshot (version ${data.version || 'n/a'})`);
  }
  document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
  document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

  // ---------- Auto-rename detection ----------
  function norm(s){ return (s||"").trim().toLowerCase(); }

  function detectRoleRenames(currentRoles){
    const before = new Set((ORIGINAL.roles||[]).map(r => norm(r.name)));
    const renames = [];
    if ((ORIGINAL.roles||[]).length === currentRoles.length){
      for (let i=0;i<currentRoles.length;i++){
        const was = norm((ORIGINAL.roles[i]||{}).name);
        const now = norm((currentRoles[i]||{}).name);
        if (was && now && was !== now && before.has(was)){
          renames.push({from:(ORIGINAL.roles[i].name||""), to:(currentRoles[i].name||"")});
        }
      }
    }
    return renames;
  }

  function detectCategoryRenames(currentCats){
    const before = (ORIGINAL.categories||[]).map(c => (typeof c==='string')? c : (c?.name||""));
    const renames = [];
    if (before.length === currentCats.length){
      for (let i=0;i<currentCats.length;i++){
        const was = (typeof before[i]==='string')? before[i] : (before[i]||"");
        const now = currentCats[i]||"";
        if (norm(was) && norm(now) && norm(was) !== norm(now)){
          renames.push({from: was, to: now});
        }
      }
    }
    return renames;
  }

  function detectChannelRenames(currentChans){
    const beforeByCat = {};
    (ORIGINAL.channels||[]).forEach(ch => {
      const k = (ch.category||"");
      (beforeByCat[k] = beforeByCat[k] || []).push(ch);
    });
    const nowByCat = {};
    (currentChans||[]).forEach(ch => {
      const k = (ch.category||"");
      (nowByCat[k] = nowByCat[k] || []).push(ch);
    });
    const renames = [];
    for (const k of new Set([...Object.keys(beforeByCat), ...Object.keys(nowByCat)])){
      const A = beforeByCat[k] || [];
      const B = nowByCat[k] || [];
      const n = Math.min(A.length, B.length);
      for (let i=0;i<n;i++){
        const was = norm(A[i].name), now = norm(B[i].name);
        if (was && now && was !== now){
          renames.push({from: A[i].name||"", to: B[i].name||""});
        }
      }
    }
    return renames;
  }

  // ---------- Saver ----------
  async function saveLayout(){
    const gid = document.getElementById('guild_id').value.trim();
    if (!gid) return alert('Enter Guild ID');

    const mode = document.querySelector('input[name="mode"]:checked')?.value || 'update';

    const roles = [];
    document.querySelectorAll('#roles .role-item').forEach(r => {
      const name = r.querySelector('input[name="role_name"]').value.trim();
      const color = r.querySelector('input[name="role_color"]').value || '#000000';
      if (name) roles.push({ name, color });
    });

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
          channels.push({ name: nm, type: tp, category: cname, options: { topic, nsfw, slowmode: slow } });
        }
      });
    });

    const prune = {
      roles: document.getElementById('prune_roles').checked,
      categories: document.getElementById('prune_categories').checked,
      channels: document.getElementById('prune_channels').checked,
    };

    const manualRenames = {
      roles: collectRenames('roleRenames'),
      categories: collectRenames('catRenames'),
      channels: collectRenames('chanRenames'),
    };

    const autoRenames = {
      roles: detectRoleRenames(roles),
      categories: detectCategoryRenames(categories),
      channels: detectChannelRenames(channels),
    };

    function mergeRen(a, b){
      const key = x => `${(x.from||"").toLowerCase()}→${(x.to||"").toLowerCase()}`;
      const seen = new Set(a.map(key));
      return a.concat(b.filter(x => !seen.has(key(x))));
    }
    const renames = {
      roles: mergeRen(manualRenames.roles, autoRenames.roles),
      categories: mergeRen(manualRenames.categories, autoRenames.categories),
      channels: mergeRen(manualRenames.channels, autoRenames.channels),
    };

    const payload = { mode, roles, categories, channels, prune, renames };

    const res = await fetch(`/api/layout/${gid}?v=${window.__VER__}`, {
      method: 'POST',
      headers: { 'Content-Type':'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok && data.no_change) alert(`No changes detected. Current version is still ${data.version}.`);
    else if (data.ok) alert(`Saved version ${data.version}`);
    else alert(data.error || 'Error');
  }

  document.getElementById('saveBtn').addEventListener('click', saveLayout);
  document.addEventListener('DOMContentLoaded', rewireDragAndDrop);
</script>
{% endraw %}
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
    return render_template_string(_FORM_HTML, ver=str(int(time.time())))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))