# bot/sync_worker.py
import os
import time
import requests
import psycopg
from psycopg.rows import dict_row
from datetime import datetime, timezone, timedelta

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

def get_twitch_schedule(user_id, access_token):
    """Fetch Twitch schedule for a user_id."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": TWITCH_CLIENT_ID
    }
    r = requests.get(f"https://api.twitch.tv/helix/schedule?broadcaster_id={user_id}", headers=headers)
    if r.status_code != 200:
        raise RuntimeError(f"Twitch API error {r.status_code}: {r.text}")
    return r.json().get("data", {}).get("segments", [])

def create_discord_event(guild_id, event):
    """Create or update Discord scheduled event."""
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "name": event["title"],
        "scheduled_start_time": event["start_time"],
        "scheduled_end_time": event.get("end_time"),
        "privacy_level": 2,
        "entity_type": 3,
        "description": event.get("description", ""),
    }
    r = requests.post(f"https://discord.com/api/v10/guilds/{guild_id}/scheduled-events", headers=headers, json=payload)
    if r.status_code not in (200, 201):
        print(f"❌ Discord event create failed ({r.status_code}): {r.text}")

def main():
    print("⏰ Running Twitch→Discord schedule sync...")

    try:
        with psycopg.connect(DATABASE_URL, sslmode="require", row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT guild_id, twitch_user_id, access_token FROM twitch_tokens")
                tokens = cur.fetchall()

        if not tokens:
            print("⚠️ No Twitch tokens found.")
            return

        print(f"✅ Found {len(tokens)} linked Twitch accounts.")

        for row in tokens:
            guild_id = row["guild_id"]
            twitch_id = row["twitch_user_id"]
            access_token = row["access_token"]

            try:
                schedule = get_twitch_schedule(twitch_id, access_token)
                print(f"📅 {len(schedule)} event(s) found for Twitch user {twitch_id}")
                for event in schedule:
                    start = event.get("start_time")
                    if start:
                        create_discord_event(guild_id, event)
                        time.sleep(0.5)
                print(f"🔄 Synced {len(schedule)} events for guild {guild_id}")
            except Exception as e:
                print(f"❌ Failed to sync guild {guild_id}: {e}")

    except Exception as e:
        print(f"❌ Sync run failed: {e}")

    print("😴 Sleeping 1 hour...")
    time.sleep(3600)

if __name__ == "__main__":
    while True:
        main()