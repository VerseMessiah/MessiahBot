from flask import Flask, render_template, request, redirect, url_for
import json
import os

app = Flask(__name__)
CONFIG_FILE = "channel_config.json"

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

@app.route("/", methods=["GET", "POST"])
def index():
    config = load_config()
    if request.method == "POST":
        updated_config = {}
        for channel, desc in request.form.items():
            updated_config[channel] = desc.strip()
        save_config(updated_config)
        return redirect(url_for("index"))
    return render_template("index.html", config=config)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
