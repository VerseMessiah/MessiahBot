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
  <title>MessiahBot — Server Builder</title>
  <style>
    :root { --card:#f7f7fb; --border:#e3e3ee; --muted:#666; }
    body{font-family:sans-serif;max-width:1150px;margin:24px auto;padding:0 12px}
    h1{margin-bottom:8px}
    .row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
    .stack{display:flex;flex-direction:column;gap:12px}
    fieldset{margin:16px 0;padding:12px;border-radius:8px;border:1px solid var(--border)}
    button{margin-top:6px}
    .pill{font-size:12px;padding:2px 8px;border-radius:999px;background:#eef}
    .card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:10px}
    .cat-header{display:flex;align-items:center;gap:8px;justify-content:space-between}
    .cat-title{display:flex;gap:8px;align-items:center}
    .muted{color:var(--muted);font-size:12px}
    .handle{cursor:grab;user-select:none}
    .small{font-size:12px}
    .inline{display:inline-flex;gap:6px;align-items:center}
    .list{display:flex;flex-direction:column;gap:8px;min-height:4px}
    .rowbox{background:white;border:1px dashed var(--border);border-radius:10px;padding:8px;display:flex;gap:8px;align-items:flex-start;justify-content:space-between}
    .left{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
    .col{display:flex;flex-direction:column;gap:6px}
    .section{margin-top:20px}
    input[type="text"], input[type="number"]{padding:6px 8px;border:1px solid var(--border);border-radius:8px;min-width:160px}
    select{padding:6px 8px;border:1px solid var(--border);border-radius:8px}
    details > summary { cursor: pointer; user-select: none; }
    .perm-grid{display:grid;grid-template-columns: 160px 110px 110px 110px;gap:6px;align-items:center}
    .perm-grid .hdr{font-weight:600}
    .note{background:#fffbdd;border:1px solid #ffe08a;padding:8px;border-radius:8px}
    .chip{border:1px solid var(--border);background:#fff;border-radius:999px;padding:4px 8px}
  </style>
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>
</head>
<body>
  <h1>🧱 MessiahBot — Server Builder</h1>
  <p class="muted">Reorder categories by dragging their card. Drag channels within/between categories. Use the <strong>x</strong> to remove items from the payload; global prune toggles control deletion on update.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm" class="stack">
    <div class="row">
      <label>Guild ID <input type="text" id="guild_id" required></label>
      <span class="pill" id="loadHint">Load “Live” to pull current server structure.</span>
    </div>

    <div class="row">
      <button type="button" id="loadLiveBtn">Load From Live Server</button>
      <button type="button" id="loadLatestBtn">Load Latest From DB</button>
      <button type="button" id="addCategoryBtn">Add Category</button>
      <button type="button" id="addRoleBtn">Add Role</button>
      <button type="button" id="saveBtn">Save Layout</button>
    </div>

    <fieldset>
      <legend>Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <!-- COMMUNITY SETTINGS -->
    <fieldset>
      <legend>Community Settings</legend>
      <label class="inline"><input type="checkbox" id="community_enable_on_build"> Enable Community on Build</label>
      <details>
        <summary class="small">Advanced (applies on Update if server is already Community)</summary>
        <div class="row">
          <label>Verification
            <select id="community_verification">
              <option value="">(leave as-is)</option>
              <option value="none">None</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="very_high">Very High</option>
            </select>
          </label>
          <label>Default Notifications
            <select id="community_notifications">
              <option value="">(leave as-is)</option>
              <option value="all_messages">All Messages</option>
              <option value="only_mentions">Only @mentions</option>
            </select>
          </label>
          <label>Explicit Media Filter
            <select id="community_explicit_filter">
              <option value="">(leave as-is)</option>
              <option value="disabled">Disabled</option>
              <option value="members_without_roles">Members w/o roles</option>
              <option value="all_members">All members</option>
            </select>
          </label>
        </div>
        <div class="row">
          <label>Rules Channel (name) <input type="text" id="community_rules_channel" placeholder="e.g. rules"></label>
          <label>Community Updates Channel (name) <input type="text" id="community_updates_channel" placeholder="e.g. announcements"></label>
        </div>
        <p class="muted small">If channels don’t exist yet, the bot will create them and set them as the system channels.</p>
      </details>
    </fieldset>

    <section class="section">
      <h3>Roles</h3>
      <div id="roles" class="list"></div>
      <p class="small muted">Set colors, optional rename, and a few default permissions per role.</p>
    </section>

    <section class="section">
      <h3>Categories & Channels</h3>
      <div id="categories" class="stack"></div>
    </section>

    <section class="section">
      <h3>Danger Zone</h3>
      <p class="note small">
        <strong>Delete not listed</strong>: when enabled, anything you don’t include in this form will be removed on update.
      </p>
      <label class="inline"><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
      <label class="inline"><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
      <label class="inline"><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>
    </section>
  </form>

<script>
const $ = sel => document.querySelector(sel);
const el = (tag, attrs={}, children=[]) => {
  const node = document.createElement(tag);
  for (const [k,v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.substring(2), v);
    else if (k === 'html') node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  (Array.isArray(children)?children:[children]).forEach(c=>{
    if (c==null) return;
    node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return node;
};
const norm = s => (s||'').trim();

/* ------- Role row with quick default permissions ------- */
function rolePermToggles(perms = {}){
  // curated role-level defaults
  const keys = [
    ['admin','Admin'],
    ['manage_channels','Manage Channels'],
    ['manage_roles','Manage Roles'],
    ['view_channel','View'],
    ['send_messages','Send'],
    ['connect','Connect (voice)'],
    ['speak','Speak (voice)'],
  ];
  const wrap = el('div', {class:'row small'});
  keys.forEach(([k,label])=>{
    const id = `rp_${k}_${Math.random().toString(36).slice(2,7)}`;
    wrap.append(
      el('label', {class:'inline', for:id}, [
        el('input', {type:'checkbox', id, class:'role-perm', dataset:{key:k}, ...(perms[k] ? {checked:true}:{})}),
        label
      ])
    );
  });
  return wrap;
}
function roleRow(name="", color="#000000", renameTo="", perms = {}) {
  const row = el('div', {class:'rowbox role'});
  const left = el('div', {class:'left'}, [
    el('span', {class:'handle', title:'Drag'}, '⠿'),
    el('input', {type:'text', placeholder:'Role name', value:name, class:'role-name'}),
    el('input', {type:'color', value:color || '#000000', class:'role-color'}),
    el('input', {type:'text', placeholder:'Rename → (optional)', value:renameTo, class:'role-rename small', style:'min-width:140px'})
  ]);
  const right = el('div', {class:'col'}, [
    rolePermToggles(perms),
    el('div', {class:'row', style:'justify-content:flex-end'}, [
      el('button', {type:'button', class:'small', onclick:()=>row.remove()}, 'x')
    ])
  ]);
  row.append(left, right);
  return row;
}

/* ------- Tri-state overwrite widget (inherit/allow/deny) ------- */
function triSelect(val="inherit"){
  const s = el('select', {class:'ow-val'});
  [['inherit','inherit'],['allow','allow'],['deny','deny']].forEach(([v,l])=>{
    s.append(el('option', {value:v, ...(v===val?{selected:true}:{})}, l));
  });
  return s;
}
function overwriteEditor(rolesList = [], existing = {}){
  // existing: { role_name: {view:'allow|deny|inherit', send:..., connect:..., speak:..., manage_channels:..., manage_roles:...} }
  const perms = ['view','send','connect','speak','manage_channels','manage_roles'];
  const grid = el('div', {class:'perm-grid'});
  grid.append(el('div', {class:'hdr'}, 'Role'));
  perms.forEach(p=>grid.append(el('div', {class:'hdr'}, p.replace('_',' '))));

  rolesList.forEach(r=>{
    const rn = r.name || r;
    const row = existing[rn] || {};
    grid.append(el('div', {}, rn));
    perms.forEach(p=>{
      grid.append(triSelect(row[p] || 'inherit'));
    });
  });
  return grid;
}

/* ------- Channel row with options + overwrites ------- */
function channelRow(name="", type="text", categoryName="", renameTo="", options = {}, ow = {}) {
  const row = el('div', {class:'rowbox chan', dataset:{channelType:type}});
  const opts = {
    nsfw: !!options.nsfw,
    age_restricted: !!options.age_restricted,
    slowmode: options.slowmode || 0,
    announcement: !!options.announcement,
    topic: options.topic || ""
  };

  const leftTop = el('div', {class:'left'}, [
    el('span', {class:'handle', title:'Drag'}, '⠿'),
    el('input', {type:'text', placeholder:'Channel name', value:name, class:'ch-name'}),
    el('select', {class:'ch-type'}, [
      el('option', {value:'text', ...(type==='text'?{selected:true}:{})}, 'text'),
      el('option', {value:'voice', ...(type==='voice'?{selected:true}:{})}, 'voice'),
      el('option', {value:'forum', ...(type==='forum'?{selected:true}:{})}, 'forum'),
      el('option', {value:'announcement', ...(type==='announcement'?{selected:true}:{})}, 'announcement'),
    ]),
    el('input', {type:'text', placeholder:'Rename → (optional)', value:renameTo, class:'ch-rename small', style:'min-width:140px'}),
  ]);

  const rightTop = el('div', {class:'row small'}, [
    el('label', {class:'inline'}, [el('input', {type:'checkbox', class:'ch-nsfw', ...(opts.nsfw?{checked:true}:{})}), 'NSFW']),
    el('label', {class:'inline'}, [el('input', {type:'checkbox', class:'ch-age', ...(opts.age_restricted?{checked:true}:{})}), 'Age-restricted']),
    el('label', {class:'inline'}, ['Slowmode (s) ', el('input', {type:'number', min:'0', max:'21600', value:String(opts.slowmode), class:'ch-slowmode', style:'width:90px'})]),
  ]);

  const topicRow = el('div', {class:'row small'}, [
    el('label', {}, ['Topic ', el('input', {type:'text', class:'ch-topic', value:opts.topic, placeholder:'Optional channel topic', style:'min-width:260px'})]),
    el('span', {class:'muted small'}, 'Announcement toggle is implied if type=announcement')
  ]);

  const overWrap = el('details', {}, [
    el('summary', {class:'small'}, 'Permissions (overrides)'),
    el('div', {class:'permHost'})
  ]);

  const right = el('div', {class:'col', style:'flex:1;'}, [
    el('div', {class:'row', style:'justify-content:flex-end'}, [ el('button', {type:'button', class:'small', onclick:()=>row.remove()}, 'x') ]),
    rightTop,
    topicRow,
    overWrap
  ]);

  row.append(leftTop, right);

  // perms will be filled later when we know the roles list
  row.dataset.parentCategory = categoryName || "";
  row._owExisting = ow || {};
  return row;
}

/* ------- Category card with overwrites per category + channels list ------- */
function categoryCard(name="", renameTo="", catOw = {}) {
  const card = el('div', {class:'card cat', draggable:false});
  const header = el('div', {class:'cat-header'}, [
    el('div', {class:'cat-title'}, [
      el('span', {class:'handle', title:'Drag'}, '⠿'),
      el('input', {type:'text', placeholder:'Category name', value:name, class:'cat-name'}),
      el('input', {type:'text', placeholder:'Rename → (optional)', value:renameTo, class:'cat-rename small', style:'min-width:160px'})
    ]),
    el('div', {}, [ el('button', {type:'button', class:'small', onclick:()=>card.remove()}, 'x') ])
  ]);

  const catOver = el('details', {}, [
    el('summary', {class:'small'}, 'Permissions (category overwrites)'),
    el('div', {class:'catPermHost'})
  ]);

  const chanList = el('div', {class:'list channels'});
  const addChanBtn = el('button', {type:'button', class:'small', onclick:()=>chanList.appendChild(channelRow("", "text", name))}, '+ add channel');

  card.append(header, catOver, el('div', {class:'muted small'}, 'Drag channels here to move them into this category.'), chanList, addChanBtn);

  new Sortable(chanList, {
    group: 'channels',
    animation: 150,
    handle: '.handle',
    onAdd: (evt)=> {
      const parentName = card.querySelector('.cat-name').value || "";
      evt.item.dataset.parentCategory = parentName;
    }
  });

  // store existing ow until roles are known
  card._catOwExisting = catOw || {};
  return card;
}

/* ------- Sortables & helpers ------- */
function makeSortable() {
  new Sortable($('#roles'), { animation:150, handle:'.handle' });
  new Sortable($('#categories'), { animation:150, handle:'.handle', onEnd: ()=> fixChannelParentNames() });
}
function fixChannelParentNames() {
  document.querySelectorAll('#categories .card').forEach(card=>{
    const cname = card.querySelector('.cat-name').value || "";
    card.querySelectorAll('.channels .chan').forEach(ch=>{
      ch.dataset.parentCategory = cname;
    });
  });
}
document.addEventListener('input', (e)=>{
  if (e.target && e.target.classList.contains('cat-name')) fixChannelParentNames();
});

/* ------- Adders ------- */
$('#addCategoryBtn').addEventListener('click', ()=>{
  $('#categories').appendChild(categoryCard());
  fixChannelParentNames();
});
$('#addRoleBtn').addEventListener('click', ()=>{
  $('#roles').appendChild(roleRow());
});

/* ------- Build overwrite editors once roles are known ------- */
function buildOverwriteEditors() {
  const roles = collectRolesForOw(); // {name,color,perms}
  const compact = roles.map(r=>({name:r.name}));

  // Categories
  document.querySelectorAll('#categories .card').forEach(card=>{
    const host = card.querySelector('.catPermHost');
    host.innerHTML = '';
    host.appendChild(overwriteEditor(compact, card._catOwExisting || {}));
  });
  // Channels
  document.querySelectorAll('#categories .card .channels .chan').forEach(row=>{
    const host = row.querySelector('.permHost');
    host.innerHTML = '';
    host.appendChild(overwriteEditor(compact, row._owExisting || {}));
  });
}
function collectRolesForOw(){
  const roles = [];
  document.querySelectorAll('#roles .rowbox.role').forEach(row=>{
    const name = norm(row.querySelector('.role-name').value);
    const color = row.querySelector('.role-color').value || '#000000';
    const perms = {};
    row.querySelectorAll('.role-perm').forEach(cb=>{
      const key = cb.dataset.key;
      perms[key] = cb.checked ? true : false;
    });
    if (name) roles.push({name, color, perms});
  });
  return roles;
}

/* ------- Hydration ------- */
function clearList(node){ while(node.firstChild) node.removeChild(node.firstChild); }

function hydrate(payload) {
  const mode = (payload.mode || 'build');
  const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
  if (radio) radio.checked = true;

  // community
  $('#community_enable_on_build').checked = !!(payload.community && payload.community.enable_on_build);
  $('#community_verification').value = (payload.community && payload.community.settings && payload.community.settings.verification) || '';
  $('#community_notifications').value = (payload.community && payload.community.settings && payload.community.settings.notifications) || '';
  $('#community_explicit_filter').value = (payload.community && payload.community.settings && payload.community.settings.explicit_filter) || '';
  $('#community_rules_channel').value = (payload.community && payload.community.settings && payload.community.settings.rules_channel) || '';
  $('#community_updates_channel').value = (payload.community && payload.community.settings && payload.community.settings.updates_channel) || '';

  clearList($('#roles'));
  (payload.roles || []).forEach(r=>{
    $('#roles').appendChild(roleRow(r.name||"", r.color||"#000000", "", r.perms||{}));
  });
  if ((payload.roles||[]).length===0) $('#roles').appendChild(roleRow());

  clearList($('#categories'));
  const cats = (payload.categories || []);
  const channels = (payload.channels || []);

  // categories may be strings or objects {name, overwrites}
  cats.forEach(c=>{
    const name = (typeof c === 'string') ? c : (c && c.name ? c.name : "");
    const catOw = (typeof c === 'object' && c.overwrites) ? c.overwrites : {};
    $('#categories').appendChild(categoryCard(name, "", catOw));
  });

  // place channels into categories (or Uncategorized)
  channels.forEach(ch=>{
    const parent = (ch.category || "").toLowerCase();
    const name = ch.name || "";
    const type = (ch.type || 'text');
    const opts = ch.options || {};
    const ow = ch.overwrites || {};

    let target = null;
    document.querySelectorAll('#categories .card').forEach(card=>{
      const cname = (card.querySelector('.cat-name').value || "").toLowerCase();
      if (cname === parent) target = card;
    });
    if (!target) {
      let unc = document.querySelector('#categories .card[data-unc="1"]');
      if (!unc) {
        unc = categoryCard("Uncategorized", "");
        unc.dataset.unc = "1";
        $('#categories').appendChild(unc);
      }
      target = unc;
    }
    target.querySelector('.channels').appendChild(channelRow(name, type, parent, "", opts, ow));
  });

  $('#prune_roles').checked = !!(payload.prune && payload.prune.roles);
  $('#prune_categories').checked = !!(payload.prune && payload.prune.categories);
  $('#prune_channels').checked = !!(payload.prune && payload.prune.channels);

  // after roles exist, build overwrite editors
  buildOverwriteEditors();
  fixChannelParentNames();
}

/* ------- Collect payload ------- */
function collectPermGrid(host){
  const out = {};
  // layout: [Role] [view] [send] [connect] [speak] [manage_channels] [manage_roles]
  const rows = Array.from(host.querySelectorAll('.perm-grid'));
  if (rows.length === 0) return out;
  const grid = rows[0];
  const children = Array.from(grid.children);
  // 7 columns per row (Role + 6 perms)
  for (let i = 7; i < children.length; i += 7){
    const roleName = children[i].textContent.trim();
    const vals = {
      view: children[i+1].querySelector('select').value,
      send: children[i+2].querySelector('select').value,
      connect: children[i+3].querySelector('select').value,
      speak: children[i+4].querySelector('select').value,
      manage_channels: children[i+5].querySelector('select').value,
      manage_roles: children[i+6].querySelector('select').value,
    };
    out[roleName] = vals;
  }
  return out;
}

function collectPayload() {
  const form = $('#layoutForm');
  const gid = $('#guild_id').value.trim();
  if (!gid) { alert('Enter Guild ID'); throw new Error('no gid'); }
  const mode = form.mode.value;

  // roles w/ default perms + renames
  const roles = [];
  document.querySelectorAll('#roles .rowbox.role').forEach(row=>{
    const name = norm(row.querySelector('.role-name').value);
    const color = row.querySelector('.role-color').value || '#000000';
    const renameTo = norm(row.querySelector('.role-rename').value);
    const perms = {};
    row.querySelectorAll('.role-perm').forEach(cb=>{
      const key = cb.dataset.key;
      perms[key] = cb.checked ? true : false;
    });
    if (!name) return;
    const r = { name, color, perms };
    if (renameTo) r._renameTo = renameTo; // converted to renames below
    roles.push(r);
  });

  const categories = [];
  const channels = [];
  const renames = { roles:[], categories:[], channels:[] };

  document.querySelectorAll('#categories .card').forEach(card=>{
    const name = norm(card.querySelector('.cat-name').value);
    const renameTo = norm(card.querySelector('.cat-rename').value);
    const catOw = collectPermGrid(card.querySelector('.catPermHost'));

    if (name) {
      const catObj = Object.keys(catOw).length ? {name, overwrites: catOw} : name;
      categories.push(catObj);
    }
    if (renameTo) renames.categories.push({from: name, to: renameTo});

    card.querySelectorAll('.channels .chan').forEach(row=>{
      const chName = norm(row.querySelector('.ch-name').value);
      const chType = row.querySelector('.ch-type').value;
      const parent = norm(card.querySelector('.cat-name').value);

      const opts = {
        nsfw: row.querySelector('.ch-nsfw').checked,
        age_restricted: row.querySelector('.ch-age').checked,
        slowmode: Number(row.querySelector('.ch-slowmode').value || 0),
        announcement: (chType === 'announcement'),
        topic: norm(row.querySelector('.ch-topic').value)
      };
      const ow = collectPermGrid(row.querySelector('.permHost'));
      const renameC = norm(row.querySelector('.ch-rename').value);

      if (chName) channels.push({ name: chName, type: chType, category: parent, options: opts, overwrites: ow });
      if (renameC) renames.channels.push({from: chName, to: renameC, category: parent});
    });
  });

  // turn role-level _renameTo into renames
  roles.forEach(r=>{
    if (r._renameTo) renames.roles.push({from: r.name, to: r._renameTo});
    delete r._renameTo;
  });

  const prune = {
    roles: $('#prune_roles').checked,
    categories: $('#prune_categories').checked,
    channels: $('#prune_channels').checked
  };

  const community = {
    enable_on_build: $('#community_enable_on_build').checked,
    settings: {
      verification: $('#community_verification').value || null,
      notifications: $('#community_notifications').value || null,
      explicit_filter: $('#community_explicit_filter').value || null,
      rules_channel: $('#community_rules_channel').value || null,
      updates_channel: $('#community_updates_channel').value || null
    }
  };

  return { mode, roles, categories, channels, prune, renames, community };
}

/* ------- Load/Save ------- */
async function loadLatest() {
  const gid = $('#guild_id').value.trim();
  if (!gid) { alert('Enter Guild ID'); return; }
  const res = await fetch(`/api/layout/${gid}/latest`);
  const data = await res.json();
  if (!data.ok) { alert(data.error || 'No layout'); return; }
  hydrate(data.payload || {});
  alert(`Loaded version ${data.version} from DB`);
}
async function loadLive() {
  const gid = $('#guild_id').value.trim();
  if (!gid) { alert('Enter Guild ID'); return; }
  const res = await fetch(`/api/live_layout/${gid}`);
  const data = await res.json();
  if (!data.ok) { alert(data.error || 'Failed to load live server'); return; }
  hydrate(data.payload || {});
  alert('Loaded from live server');
}
document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);
document.getElementById('loadLiveBtn').addEventListener('click', loadLive);

async function saveLayout() {
  try{
    const gid = $('#guild_id').value.trim();
    const payload = collectPayload();
    const res = await fetch(`/api/layout/${gid}`, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.ok && data.no_change) alert(`No changes detected. Current version is still ${data.version}.`);
    else if (data.ok) alert(`Saved version ${data.version}`);
    else alert(data.error || 'Error');
  }catch(e){}
}
document.getElementById('saveBtn').addEventListener('click', saveLayout);

/* ------- Init ------- */
document.addEventListener('DOMContentLoaded', ()=>{
  $('#roles').appendChild(roleRow());
  $('#categories').appendChild(categoryCard("General"));
  makeSortable();
  buildOverwriteEditors();
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
    # Local dev runner. On Render, you can also use:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
