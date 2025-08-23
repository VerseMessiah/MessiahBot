# bot/dashboard_messiah.py
import os, json
from flask import Flask, request, jsonify, render_template_string

# Optional Postgres (recommended in production)
DATABASE_URL = os.getenv("DATABASE_URL")
_psyco_ok = False
if DATABASE_URL:
    try:
        import psycopg2, psycopg2.extras  # type: ignore
        _psyco_ok = True
    except Exception:
        _psyco_ok = False

app = Flask(__name__)

def _db_exec(q: str, p=()):
    if not (_psyco_ok and DATABASE_URL):
        raise RuntimeError("DATABASE_URL not configured or psycopg2 not available")
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        cur = conn.cursor()
        cur.execute(q, p)
        conn.commit()
    finally:
        conn.close()

def _db_one(q: str, p=()):
    if not (_psyco_ok and DATABASE_URL):
        return None
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(q, p)
        return cur.fetchone()
    finally:
        conn.close()

@app.post("/api/layout/<guild_id>")
def save_layout(guild_id):
    """Save a new version of the layout payload for a guild."""
    if not (_psyco_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "No JSON payload"}), 400

    row = _db_one("SELECT COALESCE(MAX(version),0)+1 AS v FROM builder_layouts WHERE guild_id=%s", (guild_id,))
    version = (row or {}).get("v", 1)

    _db_exec(
        "INSERT INTO builder_layouts(guild_id, version, payload) VALUES (%s,%s,%s::jsonb)",
        (guild_id, version, json.dumps(data)),
    )
    return jsonify({"ok": True, "version": int(version)})

@app.get("/api/layout/<guild_id>/latest")
def get_latest_layout(guild_id):
    """Fetch latest saved layout for a guild."""
    if not (_psyco_ok and DATABASE_URL):
        return jsonify({"ok": False, "error": "Database not configured"}), 500

    row = _db_one(
        "SELECT version, payload FROM builder_layouts WHERE guild_id=%s ORDER BY version DESC LIMIT 1",
        (guild_id,),
    )
    if not row:
        return jsonify({"ok": False, "error": "No layout"}), 404
    return jsonify({"ok": True, "version": int(row["version"]), "payload": row["payload"]})

# Simple built-in form for quick testing
_FORM_HTML = r"""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>MessiahBot — Submit Server Layout</title></head>
<body>
  <h1>🧱 MessiahBot — Submit Server Layout</h1>
  <form id="layoutForm">
    <label>Guild ID <input type="text" id="guild_id" required></label>

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
    function addRole(){
      const d=document.createElement('div');
      d.innerHTML=`<input placeholder="Role" name="role_name"> <input type="color" name="role_color" value="#000000"> <button type=button onclick="this.parentElement.remove()">x</button>`;
      document.getElementById('roles').appendChild(d);
    }
    function addCat(){
      const d=document.createElement('div');
      d.innerHTML=`<input placeholder="Category" name="category"> <button type=button onclick="this.parentElement.remove()">x</button>`;
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
    document.getElementById('layoutForm').addEventListener('submit', async (e)=>{
      e.preventDefault();
      const f=e.target;
      const gid=document.getElementById('guild_id').value.trim();
      if(!gid){ alert('Enter Guild ID'); return; }

      const roles=[], cats=[], chans=[];
      const mode=f.mode.value;

      const rnames=f.querySelectorAll('input[name="role_name"]');
      const rcolors=f.querySelectorAll('input[name="role_color"]');
      for(let i=0;i<rnames.length;i++){
        if(rnames[i].value){ roles.push({name:rnames[i].value, color:rcolors[i].value}); }
      }

      f.querySelectorAll('input[name="category"]').forEach(el=>{ if(el.value) cats.push(el.value); });

      const cn=f.querySelectorAll('input[name="channel_name"]');
      const ct=f.querySelectorAll('select[name="channel_type"]');
      const cc=f.querySelectorAll('input[name="channel_category"]');
      for(let i=0;i<cn.length;i++){
        if(cn[i].value){ chans.push({ name:cn[i].value, type:ct[i].value, category:cc[i].value }); }
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

@app.get("/form")
def form():
    return render_template_string(_FORM_HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)))
