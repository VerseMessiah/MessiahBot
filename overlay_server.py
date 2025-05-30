import os
from flask import Flask, render_template_string

app = Flask(__name__)
latest_url = {"image": "https://i.imgur.com/default.png"}

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

def update_overlay(new_url):
    latest_url["image"] = new_url

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"✅ Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port)
