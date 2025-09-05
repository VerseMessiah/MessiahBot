# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

_psycopg_ok = False
try:
    import psycopg  # psycopg v3
    from psycopg.rows import dict_row
    _psycopg_ok = True
except Exception:
    _psycopg_ok = False

app = Flask(__name__)

# --- DB helpers ------------------------------------------------------------

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

# --- Diagnostics -----------------------------------------------------------

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

# --- Live snapshot (Discord REST) -----------------------------------------

@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    try:
        import requests
    except Exception:
        return jsonify({"ok": False, "error": "Install 'requests' in requirements.txt"}), 500

    if not DISCORD_BOT_TOKEN:
        return jsonify({"ok": False, "error": "DISCORD_BOT_TOKEN not set on web service"}), 500

    base = "https://discord.com/api/v10"
    headers = _discord_headers()

    r_roles = requests.get(f"{base}/guilds/{guild_id}/roles", headers=headers, timeout=20)
    if r_roles.status_code == 403:
        return jsonify({"ok": False, "error": "Forbidden: bot lacks permission or is not in this guild"}), 403
    if r_roles.status_code == 404:
        return jsonify({"ok": False, "error": "Guild not found"}), 404
    if r_roles.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord roles error {r_roles.status_code}: {r_roles.text}"}), 502
    roles_json = r_roles.json()

    r_channels = requests.get(f"{base}/guilds/{guild_id}/channels", headers=headers, timeout=20)
    if r_channels.status_code >= 400:
        return jsonify({"ok": False, "error": f"Discord channels error {r_channels.status_code}: {r_channels.text}"}), 502
    channels_json = r_channels.json()

    # roles (skip @everyone + managed)
    roles = []
    for r in roles_json:
        if r.get("managed"): continue
        if r.get("name") == "@everyone": continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # channels
    def pos(x): return x.get("position", 0)
    channels_sorted = sorted(channels_json, key=pos)
    cat_map = {c["id"]: c["name"] for c in channels_json if c.get("type") == 4}
    categories = [c["name"] for c in channels_sorted if c.get("type") == 4]

    chans = []
    for ch in channels_sorted:
        t = ch.get("type")
        if t == 0 or t == 2 or t == 15:
            parent_id = ch.get("parent_id")
            parent_name = cat_map.get(parent_id, "") if parent_id else ""
            kind = "text" if t == 0 else ("voice" if t == 2 else "forum")
            chans.append({"name": ch.get("name",""), "type": kind, "category": parent_name})

    payload = {"mode": "update", "roles": roles, "categories": categories, "channels": chans}
    return jsonify({"ok": True, "payload": payload})

# --- Layout API (DB) -------------------------------------------------------

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

# --- UI --------------------------------------------------------------------

_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Server Builder</title>
  <style>
    :root{--b:#ddd;--m:#666}
    body{font-family:sans-serif;max-width:1000px;margin:24px auto;padding:0 12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px}
    button{margin-top:6px}
    .muted{color:var(--m);font-size:12px}
    .row{margin:6px 0;padding:6px;border:1px solid var(--b);border-radius:6px;background:#fff}
    .inline{display:inline-flex;align-items:center;gap:6px;flex-wrap:wrap}
    .handle{cursor:grab;padding:0 6px;border:1px dashed var(--b);border-radius:4px;background:#f7f7f7}
    .section-title{display:flex;align-items:center;gap:8px;justify-content:space-between}
    .cat{border:1px solid var(--b);border-radius:8px;margin:10px 0;padding:10px;background:#fafafa}
    .cat-header{display:flex;align-items:center;gap:8px}
    .channel-list{min-height:10px;border:1px dashed var(--b);border-radius:6px;padding:6px;margin:6px 0;background:#fff}
    .channel{display:flex;align-items:center;gap:6px;border:1px solid var(--b);border-radius:6px;padding:6px;margin:6px 0;background:#fff}
    .pill{padding:2px 6px;border:1px solid var(--b);border-radius:20px;font-size:12px;background:#f7f7f7}
    .danger{color:#a00}
    .btn{padding:4px 8px;border:1px solid var(--b);border-radius:6px;background:#fff;cursor:pointer}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Server Builder</h1>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

    <p>
      <button type="button" id="loadLiveBtn" class="btn">Load From Live Server</button>
      <button type="button" id="loadLatestBtn" class="btn">Load Latest From DB</button>
      <button type="button" id="saveBtn" class="btn" onclick="saveLayout()">Save Layout</button>
    </p>

    <fieldset>
      <legend>Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <h2 class="section-title">
      <span>Roles</span>
      <button type="button" class="btn" onclick="addRole()">Add Role</button>
    </h2>
    <div id="roles"></div>

    <h2 class="section-title">
      <span>Categories & Channels</span>
      <button type="button" class="btn" onclick="addCategory('')">Add Category</button>
    </h2>

    <!-- categories render here; an extra 'Uncategorized' drop-area is at the end -->
    <div id="cats"></div>

    <div id="uncat" class="cat" data-cat="">
      <div class="cat-header">
        <span class="pill">Uncategorized</span>
        <span class="muted">Drop channels here to remove parent category</span>
      </div>
      <div class="channel-list" ondragover="dndAllow(event)" ondrop="dndDrop(event, this)"></div>
    </div>

    <h3>Danger Zone</h3>
    <p class="muted"><em>These options can delete or rename live items. Use carefully.</em></p>
    <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
    <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
    <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>

    <h4>Role Renames</h4>
    <div id="roleRenames"></div>
    <button type="button" class="btn" onclick="addRename('roleRenames')">Add Role Rename</button>

    <h4>Category Renames</h4>
    <div id="catRenames"></div>
    <button type="button" class="btn" onclick="addRename('catRenames')">Add Category Rename</button>

    <h4>Channel Renames</h4>
    <div id="chanRenames"></div>
    <button type="button" class="btn" onclick="addRename('chanRenames')">Add Channel Rename</button>
  </form>

  <script>
    // ---------- utils
    const byId = (id)=>document.getElementById(id);
    const clean = (v)=> (v && typeof v==='object' && 'name' in v ? v.name : (v??'')).toString().trim();
    const asColor = (v)=> {
      let s=(v??'').toString().trim();
      if(!s) return '#000000';
      if(s[0]!=='#') s='#'+s;
      return /^#[0-9a-f]{6}$/i.test(s)? s.toLowerCase() : '#000000';
    };

    // ---------- roles
    function addRole(name="", color="#000000"){
      const d = document.createElement('div');
      d.className = 'row';
      d.innerHTML = `
        <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement)">↕</span>
        <input placeholder="Role" name="role_name" value="${clean(name)}">
        <input type="color" name="role_color" value="${asColor(color)}">
        <button type="button" class="btn danger" onclick="this.parentElement.remove()">Delete</button>`;
      byId('roles').appendChild(d);
    }

    // ---------- categories & channels (nested)
    function addCategory(name){
      const catName = clean(name);
      const wrapper = document.createElement('div');
      wrapper.className = 'cat';
      wrapper.dataset.cat = catName;

      wrapper.innerHTML = `
        <div class="cat-header">
          <div class="inline">
            <span class="handle" draggable="true" ondragstart="dndStart(event, this.closest('.cat'))">↕</span>
            <input class="cat-name" placeholder="Category" value="${catName}" oninput="this.closest('.cat').dataset.cat=this.value.trim()">
            <button type="button" class="btn" onclick="addChannelTo(this.closest('.cat'), '', 'text')">Add Channel</button>
          </div>
          <div class="inline">
            <button type="button" class="btn" onclick="moveCat(this.closest('.cat'), -1)">↑</button>
            <button type="button" class="btn" onclick="moveCat(this.closest('.cat'), 1)">↓</button>
            <button type="button" class="btn danger" onclick="this.closest('.cat').remove()">Delete</button>
          </div>
        </div>
        <div class="channel-list" ondragover="dndAllow(event)" ondrop="dndDrop(event, this)"></div>
      `;
      byId('cats').appendChild(wrapper);
      return wrapper;
    }

    function moveCat(catEl, dir){
      if(!catEl) return;
      const parent = catEl.parentElement;
      if(dir < 0 && catEl.previousElementSibling){
        parent.insertBefore(catEl, catEl.previousElementSibling);
      }else if(dir > 0 && catEl.nextElementSibling){
        parent.insertBefore(catEl.nextElementSibling, catEl);
      }
    }

    function addChannelTo(catEl, name, type, categoryOverride){
      const chName = clean(name);
      const chType = (type||'text').toLowerCase();
      const list = catEl.querySelector('.channel-list');

      const row = document.createElement('div');
      row.className = 'channel';
      row.draggable = true;
      row.dataset.name = chName;
      row.dataset.type = ['text','voice','forum'].includes(chType) ? chType : 'text';

      row.innerHTML = `
        <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement)">↕</span>
        <input placeholder="Channel" name="channel_name" value="${chName}"
               oninput="this.closest('.channel').dataset.name=this.value.trim()">
        <select name="channel_type" onchange="this.closest('.channel').dataset.type=this.value">
          <option ${row.dataset.type==='text' ? 'selected':''}>text</option>
          <option ${row.dataset.type==='voice'? 'selected':''}>voice</option>
          <option ${row.dataset.type==='forum'? 'selected':''}>forum</option>
        </select>
        <button type="button" class="btn danger" onclick="this.closest('.channel').remove()">Delete</button>
      `;
      list.appendChild(row);
      return row;
    }

    // ---------- DnD (simple: remember dragged element)
    let DND_EL = null;
    function dndStart(ev, el){ DND_EL = el; ev.dataTransfer.effectAllowed='move'; }
    function dndAllow(ev){ ev.preventDefault(); ev.dataTransfer.dropEffect='move'; }
    function dndDrop(ev, dropZone){
      ev.preventDefault();
      if(!DND_EL) return;
      // Channel onto channel-list OR category box
      if(DND_EL.classList.contains('channel')){
        dropZone.appendChild(DND_EL);
      }else if(DND_EL.classList.contains('cat')){
        // dropping categories: insert before the dropZone's parent cat if any
        const parentCats = byId('cats');
        // if dropZone is a channel-list, use its closest .cat
        const targetCat = dropZone.closest('.cat');
        if(targetCat && targetCat !== DND_EL){
          parentCats.insertBefore(DND_EL, targetCat);
        }else if(!targetCat){
          // dropped in uncat area or top-level? move to end
          parentCats.appendChild(DND_EL);
        }
      }
      DND_EL = null;
    }

    // ---------- Renames
    function addRename(containerId, fromVal="", toVal=""){
      const d = document.createElement('div');
      d.className = 'row';
      d.innerHTML = `
        <input placeholder="From name" class="rename_from" value="${clean(fromVal)}">
        <span>→</span>
        <input placeholder="To name" class="rename_to" value="${clean(toVal)}">
        <button type="button" class="btn danger" onclick="this.parentElement.remove()">Delete</button>`;
      byId(containerId).appendChild(d);
    }
    function collectRenames(containerId){
      const out = [];
      byId(containerId).querySelectorAll('.row').forEach(r=>{
        const f = clean(r.querySelector('.rename_from')?.value);
        const t = clean(r.querySelector('.rename_to')?.value);
        if(f && t) out.push({from:f,to:t});
      });
      return out;
    }

    // ---------- Hydration
    function clearEl(el){ while(el.firstChild) el.removeChild(el.firstChild); }

    function hydrateForm(p){
      // reset toggles & rename sections
      ['prune_roles','prune_categories','prune_channels'].forEach(id=>{ const el=byId(id); if(el) el.checked=false; });
      ['roleRenames','catRenames','chanRenames'].forEach(id=>clearEl(byId(id)));

      // mode
      const mode = p.mode || 'build';
      const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
      if(radio) radio.checked = true;

      // roles
      clearEl(byId('roles'));
      (p.roles || []).forEach(r => addRole(clean(r.name), asColor(r.color)));
      if((p.roles||[]).length === 0) addRole();

      // categories + channels (group by category)
      clearEl(byId('cats'));
      clearEl(byId('uncat').querySelector('.channel-list'));

      const cats = Array.from(new Set((p.categories || []).map(clean))).filter(Boolean);
      const buckets = {};  // catName -> [channels]
      cats.forEach(c => buckets[c] = []);
      const uncat = [];

      (p.channels || []).forEach(ch=>{
        const nm = clean(ch.name);
        if(!nm) return;
        const tp = (ch.type||'text').toLowerCase();
        const cat = clean(ch.category);
        if(cat && buckets.hasOwnProperty(cat)){
          buckets[cat].push({name:nm, type:tp});
        }else if(cat && !buckets.hasOwnProperty(cat)){
          // channel references a category not in categories list → create it
          buckets[cat] = [{name:nm, type:tp}];
        }else{
          uncat.push({name:nm, type:tp});
        }
      });

      // render categories in order
      Object.keys(buckets).forEach(catName=>{
        // keep ordering given in cats[] first, then any new keys
        if(!cats.includes(catName)) cats.push(catName);
      });
      cats.forEach(cat=>{
        const catEl = addCategory(cat);
        (buckets[cat] || []).forEach(ch => addChannelTo(catEl, ch.name, ch.type));
      });

      // render uncategorized
      const uncatList = byId('uncat').querySelector('.channel-list');
      uncat.forEach(ch=>{
        const row = document.createElement('div');
        row.className='channel';
        row.draggable = true;
        row.dataset.name = clean(ch.name);
        row.dataset.type = ['text','voice','forum'].includes((ch.type||'').toLowerCase()) ? ch.type.toLowerCase() : 'text';
        row.innerHTML = `
          <span class="handle" draggable="true" ondragstart="dndStart(event, this.parentElement)">↕</span>
          <input placeholder="Channel" name="channel_name" value="${row.dataset.name}"
                 oninput="this.closest('.channel').dataset.name=this.value.trim()">
          <select name="channel_type" onchange="this.closest('.channel').dataset.type=this.value">
            <option ${row.dataset.type==='text'?'selected':''}>text</option>
            <option ${row.dataset.type==='voice'?'selected':''}>voice</option>
            <option ${row.dataset.type==='forum'?'selected':''}>forum</option>
          </select>
          <button type="button" class="btn danger" onclick="this.parentElement.remove()">Delete</button>`;
        uncatList.appendChild(row);
      });

      // ensure at least one category exists for convenience
      if(cats.length===0) addCategory('');
    }

    // ---------- Collect & Save
    async function saveLayout(){
      try{
        const gid = clean(byId('guild_id')?.value);
        if(!gid){ alert('Enter Guild ID'); return; }

        // roles
        const roles = [];
        byId('roles').querySelectorAll('.row').forEach(r=>{
          const name = clean(r.querySelector('input[name="role_name"]')?.value);
          const color = asColor(r.querySelector('input[name="role_color"]')?.value);
          if(name) roles.push({name, color});
        });

        // categories in on-screen order
        const categories = [];
        byId('cats').querySelectorAll('.cat').forEach(cat=>{
          const nm = clean(cat.querySelector('.cat-name')?.value || cat.dataset.cat);
          if(nm) categories.push(nm);
        });

        // channels: walk each cat (+ uncategorized)
        const channels = [];
        const pushChFromList = (listEl, categoryName) =>{
          listEl.querySelectorAll('.channel').forEach(ch=>{
            const name = clean(ch.querySelector('input[name="channel_name"]')?.value || ch.dataset.name);
            let type = clean(ch.querySelector('select[name="channel_type"]')?.value || ch.dataset.type).toLowerCase();
            if(!['text','voice','forum'].includes(type)) type = 'text';
            if(name) channels.push({name, type, category: clean(categoryName)});
          });
        };

        byId('cats').querySelectorAll('.cat').forEach(cat=>{
          const catName = clean(cat.querySelector('.cat-name')?.value || cat.dataset.cat);
          pushChFromList(cat.querySelector('.channel-list'), catName);
        });
        pushChFromList(byId('uncat').querySelector('.channel-list'), '');

        const mode = document.querySelector('input[name="mode"]:checked')?.value || 'build';
        const payload = {
          mode, roles, categories, channels,
          prune: {
            roles: !!byId('prune_roles')?.checked,
            categories: !!byId('prune_categories')?.checked,
            channels: !!byId('prune_channels')?.checked
          },
          renames: {
            roles: collectRenames('roleRenames'),
            categories: collectRenames('catRenames'),
            channels: collectRenames('chanRenames')
          }
        };

        const res = await fetch(`/api/layout/${gid}`, {
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await res.json().catch(()=>({ok:false,error:'Bad JSON from server'}));
        if(!res.ok){ alert(`Save failed ${res.status}: ${data.error||res.statusText}`); return; }
        if(data.ok && data.no_change) alert(`No changes detected. Current version is still ${data.version}.`);
        else if(data.ok) alert(`Saved version ${data.version}`);
        else alert(data.error || 'Unknown server error');
      }catch(err){
        console.error('Save error', err);
        alert('Save crashed: '+(err?.message||err));
      }
    }

    // ---------- Loaders
    async function loadLatest(){
      const gid = clean(byId('guild_id')?.value);
      if(!gid){ alert('Enter Guild ID'); return; }
      const r = await fetch(`/api/layout/${gid}/latest`);
      const d = await r.json();
      if(!d.ok){ alert(d.error||'No layout'); return; }
      hydrateForm(d.payload||{});
      alert(`Loaded version ${d.version} from DB`);
    }
    async function loadLive(){
      const gid = clean(byId('guild_id')?.value);
      if(!gid){ alert('Enter Guild ID'); return; }
      const r = await fetch(`/api/live_layout/${gid}`);
      const d = await r.json();
      if(!d.ok){ alert(d.error||'Failed to load live server'); return; }
      hydrateForm(d.payload||{});
      alert('Loaded from live server');
    }

    // ---------- Boot
    document.addEventListener('DOMContentLoaded', ()=>{
      // prevent accidental submit on Enter
      byId('layoutForm').addEventListener('keydown', (e)=>{
        if(e.key==='Enter' && e.target.tagName==='INPUT') e.preventDefault();
      });
      byId('loadLatestBtn').addEventListener('click', loadLatest);
      byId('loadLiveBtn').addEventListener('click', loadLive);
      byId('saveBtn').addEventListener('click', saveLayout);

      // seed defaults
      addRole();
      const c = addCategory('');
      addChannelTo(c, '', 'text');
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

# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    # For local dev; on Render use: gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))