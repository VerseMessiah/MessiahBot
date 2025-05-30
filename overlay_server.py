import os
from flask import Flask, render_template_string
from flask import request

app = Flask(__name__)
latest_url = {"image": None}

@app.route("/pupperz-overlay")
def pupperz_overlay():
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <body style="margin:0;background:transparent;">
      <img src="{{ url }}" style="max-width: 100vw; max-height: 100vh;" />
    </body>
    </html>
    """, url=latest_url["image"])

@app.route("/update-pupper", methods=["POST"])
def update_pupper():
    data = request.get_json()
    new_url = data.get("url")
    if new_url:
        latest_url["image"] = new_url
        print(f"✅ Overlay updated with: {new_url}")
        return {"status": "success"}, 200
    return {"status": "no url provided"}, 400

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"✅ Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port)
