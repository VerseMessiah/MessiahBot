# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # required for /api/live_layout

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
        "User-Agent": "MessiahBotDashboard (dashboard, 1.0)",
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

    # channels (includes categories & all types)
    r_channels = requests.get(f"{base}/guilds/{guild_id}/channels", headers=headers, timeout=20)
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord channels error {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    # Convert to our schema
    roles = []
    for r in roles_json:
        if r.get("managed"):  # skip integration-managed
            continue
        if r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # Type mapping for our UI
    TYPE_MAP = {
        0: "text",
        2: "voice",
        4: "category",
        15: "forum",
        5: "announcement",
        13: "stage",
    }

    # Collect categories with their channels
    categories = []
    id_to_cat = {}

    # First pass: categories in position order
    for ch in sorted(channels_json, key=lambda x: x.get("position", 0)):
        if ch.get("type") == 4:
            cat = {
                "name": ch.get("name", ""),
                "channels": []
            }
            categories.append(cat)
            id_to_cat[ch.get("id")] = cat

    # Second pass: non-category channels in position order; assign to parent
    def parent_name(parent_id):
        return id_to_cat.get(parent_id, {}).get("name", "") if parent_id else ""

    for ch in sorted(channels_json, key=lambda x: x.get("position", 0)):
        t = ch.get("type")
        if t == 4:
            continue
        our_type = TYPE_MAP.get(t, "text")
        chan = {
            "name": ch.get("name", ""),
            "type": our_type,
            "topic": ch.get("topic") or ""
        }
        pid = ch.get("parent_id")
        if pid and pid in id_to_cat:
            id_to_cat[pid]["channels"].append(chan)
        else:
            # channel without category → place in a synthetic "Uncategorized" bucket
            # ensure it's first if not present
            if not categories or categories[0]["name"] != "":
                categories.insert(0, {"name": "", "channels": []})
            categories[0]["channels"].append(chan)

    payload = {
        "mode": "update",
        "roles": roles,
        "categories": categories,  # [{name, channels:[{name,type,topic}...]}...]
    }
    return jsonify({"ok": True, "payload": payload})

# --- API routes (DB-backed) -----------------------------------------------

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Only inserts a new version if the payload actually changed (de-dupe).
    Body JSON: { mode, roles[], categories:[{name,channels[]}...] }
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
  <title>MessiahBot — Server Builder</title>
  <style>
    :root{
      --bg:#0b0c10; --card:#151820; --muted:#aab1c0; --text:#e8ecf3; --accent:#6aa6ff; --danger:#e06565; --ok:#5fbf7f;
      --border:#2a2f3a;
    }
    *{box-sizing:border-box}
    body{font-family:ui-sans-serif,-apple-system,Segoe UI,Roboto,Inter,Arial; background:var(--bg); color:var(--text); margin:0}
    .wrap{max-width:1100px;margin:24px auto;padding:0 16px}
    h1{font-size:28px;margin:16px 0 8px}
    .bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
    input[type=text], input[type=url], input[type=number], textarea, select{
      background:#0f1218;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:8px 10px;
    }
    textarea{width:100%;min-height:70px}
    .btn{background:#1b2130;border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:8px;cursor:pointer}
    .btn:hover{border-color:#3a4150}
    .btn.primary{background:var(--accent);color:#fff;border-color:#4d86ff}
    .btn.success{background:var(--ok);color:#06230f}
    .btn.danger{background:var(--danger);color:#330909}
    .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin:14px 0}
    .grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    .section-title{display:flex;align-items:center;justify-content:space-between;margin:4px 0 8px}
    .muted{color:var(--muted)}
    .row{display:flex;gap:8px;align-items:center;padding:6px;border:1px dashed transparent;border-radius:8px}
    .row.dragover{border-color:#3a4150;background:#121725}
    .handle{cursor:grab;user-select:none;background:#0f1420;border:1px solid var(--border);padding:2px 8px;border-radius:6px}
    .grow{flex:1}
    .cat{border:1px solid var(--border);border-radius:10px;padding:8px;margin:10px 0;background:#11161f}
    .cat-header{display:flex;gap:8px;align-items:center;margin-bottom:6px}
    .channel-list{display:flex;flex-direction:column;gap:6px}
    .pill{font-size:12px;padding:2px 8px;border:1px solid var(--border);border-radius:999px;color:var(--muted)}
    .hint{font-size:12px;color:var(--muted)}
  </style>
</head>
<body>
<div class="wrap">
  <h1>🧱 MessiahBot — Server Builder</h1>
  <div class="bar">
    <label>Guild ID <input type="text" id="guild_id" placeholder="123456789012345678" style="width:240px"></label>
    <div class="pill">Tip: load live → tweak → save</div>
  </div>

  <div class="bar" style="margin-top:8px">
    <button class="btn" id="loadLiveBtn">Load From Live Server</button>
    <button class="btn" id="loadLatestBtn">Load Latest From DB</button>
    <div style="flex:1"></div>
    <label class="muted">Mode:
      <select id="mode">
        <option value="build">Build</option>
        <option value="update" selected>Update</option>
      </select>
    </label>
    <button class="btn success" id="saveBtn">Save Layout</button>
    <a class="btn" href="/dbcheck" target="_blank">DB Check</a>
    <a class="btn" href="/routes" target="_blank">Routes</a>
  </div>

  <div class="card">
    <div class="section-title">
      <h3>Roles</h3>
      <button class="btn" onclick="addRole()">Add Role</button>
    </div>
    <div id="roles" ondragover="rolesDragOver(event)"></div>
    <div class="hint">Drag the ↕ handle to reorder roles.</div>
  </div>

  <div class="card">
    <div class="section-title">
      <h3>Categories & Channels</h3>
      <button class="btn" onclick="addCategory()">Add Category</button>
    </div>
    <div id="cats" ondragover="catsDragOver(event)"></div>
    <div class="hint">Drag the ↕ handle on a category to reorder categories. Drag a channel’s ↕ to reorder or move between categories.</div>
  </div>
</div>

<script>
/* -------------------------- Small utilities -------------------------- */
function clean(s){ return (s||'').replace(/"/g,'&quot;'); }
function asColor(c){ c=(c||'').trim(); if(!c) return '#000000'; if(c[0]!=='#') c='#'+c; if(c.length===4){return '#'+c[1]+c[1]+c[2]+c[2]+c[3]+c[3]} return c.toLowerCase(); }

/* -------------------------- DnD state -------------------------- */
let DND_EL = null;     // element being dragged
let DND_TYPE = null;   // 'role' | 'cat' | 'chan'

function dndStart(e, el, kind){
  DND_EL = el;
  DND_TYPE = kind;
  e.dataTransfer.effectAllowed = "move";
}

function getAfterElement(container, y, selector){
  const els = [...container.querySelectorAll(selector)].filter(n => n !== DND_EL);
  let closest = { offset: Number.NEGATIVE_INFINITY, element: null };
  els.forEach(child => {
    const box = child.getBoundingClientRect();
    const offset = y - (box.top + box.height/2);
    if (offset < 0 && offset > closest.offset) { closest = {offset, element: child}; }
  });
  return closest.element;
}

/* -------------------------- Roles -------------------------- */
function addRole(name="", color="#000000"){
  const d = document.createElement('div');
  d.className = 'row';
  d.dataset.kind = 'role';
  d.draggable = true;
  d.innerHTML = `
    <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement, 'role')">↕</span>
    <input class="grow" placeholder="Role name" name="role_name" value="${clean(name)}">
    <input type="color" name="role_color" value="${asColor(color)}">
    <button type="button" class="btn danger" onclick="this.parentElement.remove()">Delete</button>`;
  document.getElementById('roles').appendChild(d);
}

function rolesDragOver(e){
  e.preventDefault();
  if (!DND_EL || DND_TYPE!=='role') return;
  const container = document.getElementById('roles');
  const afterEl = getAfterElement(container, e.clientY, '.row[data-kind="role"]');
  if (!afterEl) container.appendChild(DND_EL);
  else container.insertBefore(DND_EL, afterEl);
}

/* -------------------------- Categories + Channels -------------------------- */
function addCategory(name=""){
  const cat = document.createElement('div');
  cat.className = 'cat';
  cat.dataset.kind = 'cat';
  cat.draggable = true;

  const catId = 'channels_' + Math.random().toString(36).slice(2,8);

  cat.innerHTML = `
    <div class="cat-header row">
      <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement.parentElement, 'cat')">↕</span>
      <input class="grow" placeholder="Category name (blank = uncategorized bucket)" value="${clean(name)}">
      <button type="button" class="btn" onclick="addChannel('${catId}')">Add Channel</button>
      <button type="button" class="btn danger" onclick="this.closest('.cat').remove()">Delete Category</button>
    </div>
    <div class="channel-list" id="${catId}" ondragover="channelsDragOver(event, this)"></div>
  `;
  document.getElementById('cats').appendChild(cat);
}

function addChannel(listId, name="", type="text", topic=""){
  const row = document.createElement('div');
  row.className = 'row';
  row.dataset.kind = 'chan';
  row.draggable = true;
  row.innerHTML = `
    <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement, 'chan')">↕</span>
    <input class="grow" placeholder="Channel name" value="${clean(name)}">
    <select>
      <option ${type==='text'?'selected':''} value="text">Text</option>
      <option ${type==='voice'?'selected':''} value="voice">Voice</option>
      <option ${type==='forum'?'selected':''} value="forum">Forum</option>
      <option ${type==='announcement'?'selected':''} value="announcement">Announcement</option>
      <option ${type==='stage'?'selected':''} value="stage">Stage</option>
    </select>
    <input class="grow" placeholder="Topic / Description (optional)" value="${clean(topic)}">
    <button type="button" class="btn danger" onclick="this.parentElement.remove()">Delete</button>
  `;
  document.getElementById(listId).appendChild(row);
}

function catsDragOver(e){
  e.preventDefault();
  if (!DND_EL || DND_TYPE!=='cat') return;
  const container = document.getElementById('cats');
  const afterEl = getAfterElement(container, e.clientY, '.cat');
  if (!afterEl) container.appendChild(DND_EL);
  else container.insertBefore(DND_EL, afterEl);
}

function channelsDragOver(e, listEl){
  e.preventDefault();
  if (!DND_EL || DND_TYPE!=='chan') return;
  const afterEl = getAfterElement(listEl, e.clientY, '.row[data-kind="chan"]');
  if (!afterEl) listEl.appendChild(DND_EL);
  else listEl.insertBefore(DND_EL, afterEl);
}

/* -------------------------- Hydration -------------------------- */
function clearChildren(el){ while(el.firstChild) el.removeChild(el.firstChild); }

function hydrateForm(p){
  // mode
  document.getElementById('mode').value = (p.mode || 'update');

  // roles
  const rolesEl = document.getElementById('roles');
  clearChildren(rolesEl);
  const roles = p.roles || [];
  if (roles.length===0) addRole();
  else roles.forEach(r => addRole(r.name||"", r.color||"#000000"));

  // categories + channels (nested)
  const catsEl = document.getElementById('cats');
  clearChildren(catsEl);

  // We accept either legacy flat structure or new nested structure:
  if (Array.isArray(p.categories) && p.categories.length && typeof p.categories[0] === 'object'){
    // New nested: [{name, channels:[{name,type,topic}]}...]
    p.categories.forEach(cat => {
      addCategory(cat.name || "");
      const lastCat = catsEl.lastElementChild;
      const listId = lastCat.querySelector('.channel-list').id;
      (cat.channels || []).forEach(ch => addChannel(listId, ch.name||"", ch.type||"text", ch.topic||""));
    });
  } else {
    // Legacy: categories: [str...], channels: [{name,type,category,topic?}]
    const catNames = (p.categories || []);
    const chanList = (p.channels || []);
    // Build a map name -> channels
    const map = {};
    catNames.forEach(n => map[(n||"")] = []);
    // Gather uncategorized too
    map[""] = map[""] || [];
    chanList.forEach(ch => {
      const key = (ch.category || "");
      if (!map[key]) map[key] = [];
      map[key].push({name: ch.name||"", type: ch.type||"text", topic: ch.topic||""});
    });
    // Preserve order of categories
    const ordered = ["", ...catNames.filter(n => n!=="" )];
    ordered.forEach(catName => {
      addCategory(catName);
      const lastCat = catsEl.lastElementChild;
      const listId = lastCat.querySelector('.channel-list').id;
      (map[catName] || []).forEach(ch => addChannel(listId, ch.name, ch.type, ch.topic));
    });
  }

  // Ensure at least one category exists
  if (!catsEl.firstElementChild) addCategory("");
}

/* -------------------------- Load / Save -------------------------- */
async function loadLatest(){
  const gid = document.getElementById('guild_id').value.trim();
  if(!gid){ alert('Enter Guild ID'); return; }
  const res = await fetch(`/api/layout/${gid}/latest`);
  const data = await res.json();
  if(!data.ok){ alert(data.error || 'No layout'); return; }
  hydrateForm(data.payload || {});
  alert(`Loaded version ${data.version} from DB`);
}

async function loadLive(){
  const gid = document.getElementById('guild_id').value.trim();
  if(!gid){ alert('Enter Guild ID'); return; }
  const res = await fetch(`/api/live_layout/${gid}`);
  const data = await res.json();
  if(!data.ok){ alert(data.error || 'Failed to load live server'); return; }
  hydrateForm(data.payload || {});
  alert('Loaded from live server');
}

function collectLayout(){
  const mode = document.getElementById('mode').value;

  // roles in visual order
  const roles = [];
  document.querySelectorAll('#roles .row[data-kind="role"]').forEach(r => {
    const name = r.querySelector('input[name="role_name"]').value.trim();
    const color = r.querySelector('input[name="role_color"]').value;
    if (name) roles.push({name, color});
  });

  // categories + channels in visual order
  const categories = [];
  document.querySelectorAll('#cats .cat').forEach(catEl => {
    const name = catEl.querySelector('.cat-header input').value.trim();
    const channels = [];
    catEl.querySelectorAll('.channel-list .row[data-kind="chan"]').forEach(chEl => {
      const cName = chEl.querySelector('input[placeholder="Channel name"]').value.trim();
      const cType = chEl.querySelector('select').value;
      const topicEl = chEl.querySelector('input[placeholder^="Topic"]');
      const cTopic = topicEl ? topicEl.value.trim() : "";
      if (cName) channels.push({name: cName, type: cType, topic: cTopic});
    });
    categories.push({name, channels});
  });

  return { mode, roles, categories };
}

async function saveLayout(){
  const gid = document.getElementById('guild_id').value.trim();
  if(!gid){ alert('Enter Guild ID'); return; }

  const payload = collectLayout();
  const res = await fetch(`/api/layout/${gid}`, {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  if (data.ok && data.no_change) alert(`No changes detected. Current version is still ${data.version}.`);
  else if (data.ok) alert(`Saved version ${data.version}`);
  else alert(data.error || 'Error saving layout');
}

/* -------------------------- Wire up -------------------------- */
document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
document.getElementById('loadLiveBtn').addEventListener('click', loadLive);
document.getElementById('saveBtn').addEventListener('click', saveLayout);

// Prevent Enter from accidental form submit
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && ['INPUT','SELECT','TEXTAREA'].includes(e.target.tagName)) e.preventDefault();
});

// Start with one empty role and one empty category if blank
addRole();
addCategory("");
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