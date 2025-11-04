# bot/sync_worker.py
import os
import psycopg
import requests
import time
from datetime import datetime, timezone

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")

DISCORD_API = "https://discord.com/api/v10"
TWITCH_API = "https://api.twitch.tv/helix"

def discord_headers():
    return {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

def twitch_headers(access_token):
    return {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": TWITCH_CLIENT_ID
    }

def fetch_twitch_schedule(twitch_user_id, access_token):
    url = f"{TWITCH_API}/schedule?broadcaster_id={twitch_user_id}"
    r = requests.get(url, headers=twitch_headers(access_token), timeout=15)
    if r.status_code == 401:
        raise RuntimeError("Unauthorized – access token may have expired")
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("segments", [])

def fetch_discord_events(guild_id):
    url = f"{DISCORD_API}/guilds/{guild_id}/scheduled-events"
    r = requests.get(url, headers=discord_headers(), timeout=15)
    r.raise_for_status()
    return r.json()

def upsert_discord_event(guild_id, twitch_event):
    """Create or update a Discord scheduled event based on Twitch data."""
    title = twitch_event.get("title", "Twitch Stream")
    start = twitch_event.get("start_time")
    end = twitch_event.get("end_time")
    desc = twitch_event.get("canceled_until") or twitch_event.get("category", "Twitch Stream")
    loc = "https://twitch.tv/" + twitch_event.get("broadcaster_name", "unknown")

    payload = {
        "name": title,
        "scheduled_start_time": start,
        "scheduled_end_time": end,
        "entity_type": 3,  # external event
        "entity_metadata": {"location": loc},
        "description": desc or "Live on Twitch!",
        "privacy_level": 2
    }

    # Find existing by name
    existing = fetch_discord_events(guild_id)
    found = next((e for e in existing if e["name"] == title), None)

    if found:
        event_id = found["id"]
        url = f"{DISCORD_API}/guilds/{guild_id}/scheduled-events/{event_id}"
        r = requests.patch(url, headers=discord_headers(), json=payload)
    else:
        url = f"{DISCORD_API}/guilds/{guild_id}/scheduled-events"
        r = requests.post(url, headers=discord_headers(), json=payload)

    if r.status_code not in (200, 201):
        print(f"[WARN] Failed to upsert event: {r.status_code} {r.text}")
    else:
        print(f"[OK] Synced event: {title}")

def run_sync():
    with psycopg.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT guild_id, twitch_user_id, access_token FROM twitch_tokens WHERE access_token IS NOT NULL")
            rows = cur.fetchall()

    for (guild_id, twitch_user_id, access_token) in rows:
        print(f"🔄 Syncing Twitch schedule for guild {guild_id} (user {twitch_user_id})")
        try:
            schedule = fetch_twitch_schedule(twitch_user_id, access_token)
            for seg in schedule:
                upsert_discord_event(guild_id, seg)
            print(f"✅ Synced {len(schedule)} Twitch events for guild {guild_id}")
        except Exception as e:
            print(f"[ERROR] {guild_id}: {e}")

if __name__ == "__main__":
    while True:
        print("⏰ Running Twitch→Discord schedule sync...")
        run_sync()
        print("Sleeping 3600s...")
        time.sleep(3600)