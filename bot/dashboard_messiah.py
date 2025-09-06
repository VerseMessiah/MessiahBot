# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

_psycopg_ok = False
try:
    import psycopg  # v3
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

def _discord_headers():
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError("DISCORD_BOT_TOKEN not set on web service")
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "User-Agent": "MessiahBotDashboard/1.0",
        "Content-Type": "application/json",
    }

# ---------- probes ----------
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
            with psycopg.connect(DATABASE_URL, sslmode="require"):
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

# ---------- live snapshot via Discord REST ----------
@app.get("/api/live_layout/<guild_id>")
def live_layout(guild_id: str):
    """
    Build a layout from the current live Discord server using REST.
    Adds `original_type` to channels so the UI can lock/limit conversions.
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

    # Convert roles (skip @everyone and managed)
    roles = []
    for r in roles_json:
        if r.get("managed"):
            continue
        if r.get("name") == "@everyone":
            continue
        color_int = r.get("color") or 0
        roles.append({"name": r.get("name", ""), "color": f"#{color_int:06x}"})

    # Categories and map
    categories = [c["name"] for c in channels_json if c.get("type") == 4]
    cat_map = {c["id"]: c["name"] for c in channels_json if c.get("type") == 4}

    # helper to map Discord API types -> our UI types
    def ui_type(api_type: int) -> str:
        # https://discord.com/developers/docs/resources/channel#channel-object-channel-types
        if api_type == 0:   # text
            return "text"
        if api_type == 2:   # voice
            return "voice"
        if api_type == 4:   # category
            return "category"
        if api_type == 5:   # news/announcement
            return "announcement"
        if api_type == 15:  # forum
            return "forum"
        if api_type == 13:  # stage
            return "stage"
        return "text"

    # Sort by 'position' so order is stable
    def pos(x): return x.get("position", 0)
    channels_sorted = sorted(channels_json, key=pos)

    chans = []
    for ch in channels_sorted:
        t = ch.get("type")
        name = ch.get("name", "")
        if t == 4:
            continue  # categories handled above
        parent_id = ch.get("parent_id")
        parent_name = cat_map.get(parent_id, "") if parent_id else ""
        t_ui = ui_type(t)
        topic = ch.get("topic", "")
        chans.append({
            "name": name,
            "type": t_ui,             # desired type (UI can change this)
            "original_type": t_ui,    # live type (for validation)
            "category": parent_name,
            "topic": topic
        })

    payload = {
        "mode": "update",
        "roles": roles,
        "categories": categories,
        "channels": chans
    }
    return jsonify({"ok": True, "payload": payload})

# ---------- layout storage API ----------
@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Validates channel type changes according to original_type (if present).
    """
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    incoming = request.get_json(silent=True) or {}
    if not incoming:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    # Validate channel type conversions
    warnings = []
    channels = incoming.get("channels") or []

    def compatible(live_type: str, req_type: str) -> bool:
        if live_type in ("text", "announcement"):
            return req_type in ("text", "announcement")
        if live_type in ("voice", "stage"):
            return req_type in ("voice", "stage")
        if live_type == "forum":
            return req_type == "forum"
        return True

    for ch in channels:
        live = (ch.get("original_type") or "").lower().strip()
        req = (ch.get("type") or "text").lower().strip()
        if live and not compatible(live, req):
            ch["type"] = live
            warnings.append(
                f"Channel '{ch.get('name','')}' type reset to '{live}' (conversion from '{req}' not supported)."
            )

    latest = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if latest and _json_equal(incoming, latest["payload"]):
        return jsonify({"ok": True, "version": int(latest["version"]), "no_change": True, "warnings": warnings})

    row = _db_one(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM builder_layouts WHERE guild_id = %s",
        (guild_id,),
    )
    version = int((row or {}).get("v", 1))

    _db_exec(
        "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s, %s, %s::jsonb)",
        (guild_id, version, json.dumps(incoming)),
    )
    return jsonify({"ok": True, "version": version, "no_change": False, "warnings": warnings})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id: str):
    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No layout"}), 404
    return jsonify({"ok": True, "version": int(row["version"]), "payload": row["payload"]})

# ---------- Form UI ----------
_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Server Builder</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root { color-scheme: dark light; }
    body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,"Helvetica Neue",Arial;
         max-width:1000px;margin:24px auto;padding:0 12px;background:#0b0b0f;color:#e7e7ea}
    a{color:#8ab4ff}
    fieldset{margin:16px 0;padding:12px;border-radius:10px;border:1px solid #2a2a34}
    input,select,button{font:inherit}
    input[type="text"]{background:#11131a;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:6px 8px}
    select{background:#11131a;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:6px 8px}
    button{background:#1a1f2b;border:1px solid #2a2a34;color:#e7e7ea;border-radius:8px;padding:8px 10px;cursor:pointer}
    .row{display:flex;gap:6px;align-items:center;margin:6px 0}
    .stack{display:flex;flex-direction:column;gap:6px}
    .subtle{opacity:.8}
    .pill{display:inline-flex;gap:6px;align-items:center;padding:2px 8px;border:1px solid #2a2a34;border-radius:999px;background:#131722;font-size:12px}
    .grid{display:grid;grid-template-columns: 1fr 1fr;gap:12px}
    .list{padding:8px;border:1px dashed #2a2a34;border-radius:10px;background:#0f1219}
    .cat{padding:8px;border:1px solid #2a2a34;border-radius:10px;background:#0f1219;margin:8px 0}
    .ch{display:flex;gap:6px;align-items:center;margin:6px 0}
    .muted{opacity:.6}
  </style>
</head>
<body>
  <h1>🧱 MessiahBot — Server Builder</h1>
  <p class="subtle">Enter your Guild ID, then <strong>Load From Live</strong> or <strong>Load Snapshot</strong>, edit, and <strong>Save</strong>.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm" class="stack" name="layoutForm">
    <div class="row">
      <label>Guild ID <input type="text" id="guild_id" required placeholder="123456789012345678"></label>
      <button type="button" id="loadLatestBtn">Load Snapshot</button>
      <button type="button" id="loadLiveBtn">Load From Live</button>
      <span id="status" class="pill">idle</span>
    </div>

    <fieldset>
      <legend>Mode</legend>
      <label><input type="radio" name="mode" value="build" checked> Build</label>
      <label><input type="radio" name="mode" value="update"> Update</label>
    </fieldset>

    <section>
      <h3>Roles</h3>
      <div id="roles" class="list"></div>
      <button type="button" id="addRoleBtn">Add Role</button>
    </section>

    <section>
      <h3>Categories & Channels</h3>
      <div id="cats" class="list"></div>
      <button type="button" id="addCatBtn">Add Category</button>
    </section>

    <section>
      <h3>Danger Zone</h3>
      <label><input type="checkbox" id="prune_roles"> Delete roles not listed here</label><br>
      <label><input type="checkbox" id="prune_categories"> Delete categories not listed here (only if empty)</label><br>
      <label><input type="checkbox" id="prune_channels"> Delete channels not listed here</label>
    </section>

    <div class="row">
      <button type="button" id="saveBtn">Save Layout</button>
      <span id="saveNote" class="pill muted"></span>
    </div>
  </form>

  <script>
  // ---------- utilities ----------
  function $(sel, el){ return (el||document).querySelector(sel); }
  function $all(sel, el){ return Array.prototype.slice.call((el||document).querySelectorAll(sel)); }
  var statusPill = $("#status");
  function setStatus(txt){ statusPill.textContent = txt; }

  // ---------- roles ----------
  function addRoleRow(name, color){
    if (!name) name = "";
    if (!color) color = "#000000";
    var d = document.createElement('div');
    d.className = "row";
    d.innerHTML =
      '<input placeholder="Role" name="role_name" value="'+name+'">'+
      '<input type="color" name="role_color" value="'+color+'">'+
      '<button type="button" class="del">✕</button>';
    d.querySelector(".del").onclick = function(){ d.remove(); };
    $("#roles").appendChild(d);
  }

  // ---------- categories/channels ----------
  function catBox(name){
    if (!name) name = "";
    var wrap = document.createElement('div');
    wrap.className = "cat";
    wrap.innerHTML =
      '<div class="row">'+
        '<strong>Category</strong>'+
        '<input placeholder="Category name (blank = uncategorized bucket)" class="cat-name" value="'+name+'">'+
        '<button type="button" class="addChan">+ Channel</button>'+
        '<button type="button" class="delCat">✕</button>'+
      '</div>'+
      '<div class="stack ch-list"></div>';
    wrap.querySelector(".delCat").onclick = function(){ wrap.remove(); };
    wrap.querySelector(".addChan").onclick = function(){ $(".ch-list", wrap).appendChild(channelRow({})); };
    return wrap;
  }

  // Build a channel row. If original_type is provided, lock/limit type options.
  function channelRow(ch){
    ch = ch || {};
    var name = ch.name || "";
    var type = (ch.type || "text").toLowerCase();
    var original = ch.original_type ? ch.original_type.toLowerCase() : null;
    var topic = ch.topic || (ch.options && ch.options.topic) || "";

    var full = ["text","announcement","voice","stage","forum"];
    var opts = full.slice(0);
    var lockedLabel = "";
    if (original){
      if (["text","announcement"].indexOf(original) >= 0){ opts = ["text","announcement"]; }
      else if (["voice","stage"].indexOf(original) >= 0){ opts = ["voice","stage"]; }
      else if (original === "forum"){ opts = ["forum"]; lockedLabel = "Forum · locked"; }
    }
    var useType = (opts.indexOf(type) >= 0) ? type : (original || "text");

    var d = document.createElement('div');
    d.className = "ch";
    var selectHTML;
    if (opts.length === 1 && opts[0] === "forum"){
      selectHTML =
        '<span class="pill">'+(lockedLabel || 'Forum · locked')+'</span>'+
        '<input type="hidden" class="ch-type" value="forum">';
    } else {
      var options = '';
      for (var i=0;i<opts.length;i++){
        var o = opts[i];
        options += '<option value="'+o+'"'+(o===useType?' selected':'')+'>'+o+'</option>';
      }
      selectHTML = '<select class="ch-type">'+options+'</select>';
    }

    d.innerHTML =
      '<input class="ch-name" placeholder="Channel name" value="'+name+'">'+
      selectHTML +
      '<input class="ch-topic" placeholder="Topic / Description" value="'+topic+'">'+
      '<button type="button" class="del">✕</button>';
    d.setAttribute("data-original-type", original || "");
    d.querySelector(".del").onclick = function(){ d.remove(); };
    return d;
  }

  function hydrate(p){
    // Mode
    var mode = (p.mode || 'build');
    var radio = document.querySelector('input[name="mode"][value="' + mode + '"]');
    if (radio) radio.checked = true;

    // roles
    var R = $("#roles"); R.innerHTML = "";
    var roles = p.roles || [];
    for (var i=0;i<roles.length;i++){
      addRoleRow(roles[i].name || "", roles[i].color || "#000000");
    }
    if (roles.length === 0) addRoleRow("", "#000000");

    // categories + channels (nested preferred, else flat)
    var C = $("#cats"); C.innerHTML = "";
    if (Array.isArray(p.categories) && p.categories.length && typeof p.categories[0] === "object"){
      for (var ci=0;ci<p.categories.length;ci++){
        var cat = p.categories[ci] || {};
        var box = catBox(cat.name || "");
        var listEl = $(".ch-list", box);
        var chans = cat.channels || [];
        for (var j=0;j<chans.length;j++){
          listEl.appendChild(channelRow(chans[j]));
        }
        C.appendChild(box);
      }
    } else {
      var catNames = p.categories || [];
      var map = {};
      for (var k=0;k<catNames.length;k++){
        var nm = catNames[k] || "";
        var bx = catBox(nm);
        C.appendChild(bx);
        map[(nm||"").toLowerCase()] = $(".ch-list", bx);
      }
      var hadUn = false;
      var chansFlat = p.channels || [];
      for (var m=0;m<chansFlat.length;m++){
        var ch = chansFlat[m] || {};
        var parent = (ch.category || "").toLowerCase();
        var row = channelRow(ch);
        if (map[parent]) map[parent].appendChild(row);
        else {
          if (!hadUn){
            var u = catBox("");
            C.appendChild(u);
            map[""] = $(".ch-list", u);
            hadUn = true;
          }
          map[""].appendChild(row);
        }
      }
      if (C.children.length === 0){
        C.appendChild(catBox(""));
      }
    }

    // danger zone
    $("#prune_roles").checked = !!(p.prune && p.prune.roles);
    $("#prune_categories").checked = !!(p.prune && p.prune.categories);
    $("#prune_channels").checked = !!(p.prune && p.prune.channels);
  }

  function collectPayload(){
    var mode = document.forms.layoutForm.mode.value;

    // roles
    var roles = [];
    $all('#roles .row').forEach(function(r){
      var name = r.querySelector('input[name="role_name"]').value.trim();
      var color = r.querySelector('input[name="role_color"]').value || "#000000";
      if (name){ roles.push({name:name, color:color}); }
    });

    // categories (nested)
    var categories = [];
    $all('#cats .cat').forEach(function(catEl){
      var cname = $('.cat-name', catEl).value.trim();
      var channels = [];
      $all('.ch-list .ch', catEl).forEach(function(chEl){
        var nm = $('.ch-name', chEl).value.trim();
        if (!nm) return;
        var typeSel = $('.ch-type', chEl);
        var typ = typeSel ? typeSel.value : "forum";
        var topic = ($('.ch-topic', chEl) && $('.ch-topic', chEl).value) || "";
        var original_type = chEl.getAttribute('data-original-type') || null;
        channels.push({name:nm, type:typ, original_type:original_type, topic:topic});
      });
      categories.push({name:cname, channels:channels});
    });

    var prune = {
      roles: $("#prune_roles").checked,
      categories: $("#prune_categories").checked,
      channels: $("#prune_channels").checked
    };

    return { mode:mode, roles:roles, categories:categories, prune:prune };
  }

  // buttons
  $("#addRoleBtn").onclick = function(){ addRoleRow("", "#000000"); };
  $("#addCatBtn").onclick = function(){ $("#cats").appendChild(catBox("")); };

  $("#loadLiveBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    setStatus("loading live…");
    try{
      var res = await fetch("/api/live_layout/" + encodeURIComponent(gid));
      var data = await res.json();
      if (!data.ok){ alert(data.error || "Failed to load live"); setStatus("idle"); return; }
      hydrate(data.payload || {});
      setStatus("live loaded");
    }catch(e){
      setStatus("idle");
      alert("Failed to load live");
    }
  };

  $("#loadLatestBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    setStatus("loading snapshot…");
    try{
      var res = await fetch("/api/layout/" + encodeURIComponent(gid) + "/latest");
      var data = await res.json();
      if (!data.ok){ alert(data.error || "No layout"); setStatus("idle"); return; }
      hydrate(data.payload || {});
      setStatus("snapshot v" + data.version + " loaded");
    }catch(e){
      setStatus("idle");
      alert("Failed to load snapshot");
    }
  };

  $("#saveBtn").onclick = async function(){
    var gid = $("#guild_id").value.trim();
    if (!gid){ alert("Enter Guild ID"); return; }
    var payload = collectPayload();

    // flatten channels for legacy compatibility
    var flatChannels = [];
    for (var i=0;i<payload.categories.length;i++){
      var c = payload.categories[i];
      var cname = c.name || "";
      var chs = c.channels || [];
      for (var j=0;j<chs.length;j++){
        var ch = chs[j];
        flatChannels.push({
          name: ch.name,
          type: ch.type,
          original_type: ch.original_type || null,
          category: cname,
          options: { topic: ch.topic || "" }
        });
      }
    }

    var saveBody = {
      mode: payload.mode,
      roles: payload.roles,
      categories: payload.categories, // modern nested
      channels: flatChannels,         // legacy + validation
      prune: payload.prune
    };

    setStatus("saving…");
    try{
      var res = await fetch("/api/layout/" + encodeURIComponent(gid), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(saveBody)
      });
      var data = await res.json();
      setStatus("saved");
      if (data.warnings && data.warnings.length){
        alert("Saved with warnings:\n\n" + data.warnings.join("\n"));
      } else if (data.ok && data.no_change){
        alert("No changes detected. Current version is still " + data.version + ".");
      } else if (data.ok){
        alert("Saved version " + data.version);
      } else {
        alert(data.error || "Error");
      }
    }catch(e){
      setStatus("idle");
      alert("Failed to save");
    }
  };

  // initial rows
  addRoleRow("", "#000000");
  $("#cats").appendChild(catBox(""));
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
    # Local dev runner. On Render, prefer:
    #   gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))