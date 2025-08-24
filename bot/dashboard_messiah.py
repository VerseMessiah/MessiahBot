# bot/dashboard_messiah.py
import os
import json
from flask import Flask, request, jsonify, render_template_string

# --- Config / DB driver detection ---
DATABASE_URL = os.getenv("DATABASE_URL")

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
    """
    Execute a statement (INSERT/UPDATE/DDL). Requires psycopg v3 and DATABASE_URL.
    """
    if not (_psycopg_ok and DATABASE_URL):
        raise RuntimeError("DATABASE_URL not configured or psycopg not available")
    # autocommit=True for simple one-off statements
    with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(q, p)

def _db_one(q: str, p=()):
    """
    Fetch a single row as a dict. Returns None if DB not configured/available or no row.
    """
    if not (_psycopg_ok and DATABASE_URL):
        return None
    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(q, p)
            return cur.fetchone()

# --- Probe / utility routes ------------------------------------------------

@app.get("/ping")
def ping():
    return "pong", 200, {"Content-Type": "text/plain"}

@app.get("/routes")
def routes():
    return {"routes": sorted([str(r.rule) for r in app.url_map.iter_rules()])}

@app.get("/dbcheck")
def dbcheck():
    """
    Quick probe to verify the web service sees DATABASE_URL and has psycopg v3 installed.
    """
    ok_env = bool(DATABASE_URL)
    try:
        import psycopg  # noqa: F401  (re-check at runtime)
        ok_driver = True
        driver_version = psycopg.__version__
    except Exception:
        ok_driver = False
        driver_version = None

    # Optional: try a trivial connection if both look OK
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

# --- API routes ------------------------------------------------------------

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id: str):
    """
    Save a NEW versioned layout payload for the given guild_id.
    Body: JSON with keys: mode, roles[], categories[], channels[]
    """
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    # Next version number
    row = _db_one(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM builder_layouts WHERE guild_id = %s",
        (guild_id,),
    )
    version = int((row or {}).get("v", 1))

    _db_exec(
        "INSERT INTO builder_layouts (guild_id, version, payload) VALUES (%s, %s, %s::jsonb)",
        (guild_id, version, json.dumps(data)),
    )
    return jsonify({"ok": True, "version": version})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id: str):
    """
    Return the latest saved layout for the guild_id.
    """
    if not (_psycopg_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id = %s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No layout"}), 404
    return jsonify({"ok": True, "version": int(row["version"]), "payload": row["payload"]})

# --- Simple form UI (for manual testing) -----------------------------------

_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MessiahBot — Submit Server Layout</title>
  <style>body{font-family:sans-serif;max-width:900px;margin:24px auto;padding:0 12px}</style>
</head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <p>Enter your Guild ID, define roles/categories/channels, and submit.</p>

  <p>
    <a href="/dbcheck" target="_blank">/dbcheck</a> •
    <a href="/routes" target="_blank">/routes</a>
  </p>

  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

    <!-- ✅ NEW: Load latest from DB -->
    <p><button type="button" id="loadLatestBtn">Load Latest From DB</button></p>

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

    <p><button type="submit">Submit</button></p>
  </form>

  <script>
    // Existing add* helpers
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
                   <select name="channel_type"><option>text</option><option>voice</option><option>forum</option></select>
                   <input placeholder="Parent Category" name="channel_category">
                   <button type=button onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('chans').appendChild(d);
    }

    // ✅ NEW: Prefill helpers + loader
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
                     </select>
                     <input placeholder="Parent Category" name="channel_category" value="${category}">
                     <button type="button" onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('chans').appendChild(d);
    }

    async function loadLatest() {
      const gid = document.getElementById('guild_id').value.trim();
      if (!gid) { alert('Enter Guild ID'); return; }
      const res = await fetch(`/api/layout/${gid}/latest`);
      const data = await res.json();
      if (!data.ok) { alert(data.error || 'No layout'); return; }
      const p = data.payload || {};

      // Mode
      const mode = (p.mode || 'build');
      const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
      if (radio) radio.checked = true;

      // Roles
      clearSection('roles');
      (p.roles || []).forEach(r => addRoleRow(r.name || "", r.color || "#000000"));
      if ((p.roles || []).length === 0) addRoleRow();

      // Categories
      clearSection('cats');
      (p.categories || []).forEach(c => addCatRow(c));
      if ((p.categories || []).length === 0) addCatRow();

      // Channels
      clearSection('chans');
      (p.channels || []).forEach(ch => addChanRow(ch.name || "", (ch.type||'text'), ch.category || ""));
      if ((p.channels || []).length === 0) addChanRow();

      alert(`Loaded version ${data.version}`);
    }
    document.getElementById('loadLatestBtn').addEventListener('click', loadLatest);

    // Existing submit handler
    document.getElementById('layoutForm').addEventListener('submit', async (e)=>{
      e.preventDefault();
      const f=e.target;
      const gid=document.getElementById('guild_id').value.trim();
      if(!gid){ alert('Enter Guild ID'); return; }

      const roles=[], cats=[], chans=[];
      const mode=f.mode.value;

      const rn=f.querySelectorAll('input[name="role_name"]');
      const rc=f.querySelectorAll('input[name="role_color"]');
      for(let i=0;i<rn.length;i++){
        if(rn[i].value){ roles.push({name: rn[i].value, color: rc[i].value}); }
      }

      f.querySelectorAll('input[name="category"]').forEach(el=>{ if(el.value) cats.push(el.value); });

      const cn=f.querySelectorAll('input[name="channel_name"]');
      const ct=f.querySelectorAll('select[name="channel_type"]');
      const cc=f.querySelectorAll('input[name="channel_category"]');
      for(let i=0;i<cn.length;i++){
        if(cn[i].value){ chans.push({ name: cn[i].value, type: ct[i].value, category: cc[i].value }); }
      }

      const payload={ mode, roles, categories: cats, channels: chans };
      const res = await fetch(`/api/layout/${gid}`, {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      alert(data.ok ? `Saved version ${data.version}` : (data.error || 'Error'));
    });
  </script>
</body>
</html>
"""
@app.get("/")
def index():
    return (
        '<h1>MessiahBot Dashboard</h1>'
        '<p>Go to <a href="/form">/form</a> to submit a layout.</p>',
        200,
        {"Content-Type": "text/html"},
    )

@app.get("/form")
def form():
    return render_template_string(_FORM_HTML)

# --- Entrypoint ------------------------------------------------------------

if __name__ == "__main__":
    # Local dev runner. On Render, prefer: gunicorn "bot.dashboard_messiah:app" --bind 0.0.0.0:$PORT
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
