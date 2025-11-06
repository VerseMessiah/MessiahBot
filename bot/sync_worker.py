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
    data = r.json().get("data", {})
    segments = data.get("segments", [])
    broadcaster_name = data.get("broadcaster_name", "unknown")
    for event in segments:
        if "broadcaster_name" not in event:
            event["broadcaster_name"] = broadcaster_name
    return segments

def create_discord_event(guild_id, event):
    """Create Discord scheduled event if it doesn't already exist."""
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }

    # Fetch existing scheduled events to prevent duplicates
    existing = []
    try:
        existing_resp = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/scheduled-events",
            headers=headers
        )
        if existing_resp.status_code == 200:
            existing = existing_resp.json()
        else:
            print(f"⚠️ Could not fetch existing events: {existing_resp.text}")
    except Exception as e:
        print(f"⚠️ Error fetching existing events: {e}")

    # Determine Twitch event times
    start_time = event.get("start_time")
    end_time = event.get("end_time")
    if not end_time and start_time:
        try:
            start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end_time = (start_dt + timedelta(hours=4)).isoformat()  # ⏰ Default 4 hours
        except Exception:
            end_time = start_time  # fallback

    event_name = event.get("title", "Twitch Stream")
    existing_match = next(
        (e for e in existing if e["name"] == event_name and e["scheduled_start_time"] == start_time),
        None
    )

    if existing_match:
        print(f"⏭️ Skipping duplicate event: {event_name}")
        return

    # Build event payload
    payload = {
        "name": event_name,
        "scheduled_start_time": start_time,
        "scheduled_end_time": end_time,
        "privacy_level": 2,  # GUILD_ONLY
        "entity_type": 3,  # External event
        "entity_metadata": {
            "location": f"https://twitch.tv/{event.get('broadcaster_name', 'unknown')}"
        },
        "description": event.get("description", ""),
    }

    # Send event to Discord
    try:
        r = requests.post(
            f"https://discord.com/api/v10/guilds/{guild_id}/scheduled-events",
            headers=headers, json=payload
        )
        if r.status_code in (200, 201):
            print(f"✅ Discord event created: {event_name}")
            time.sleep(1.5)
        elif r.status_code == 429:
            retry_after = r.json().get("retry_after", 5)
            print(f"⚠️ Rate limited — sleeping {retry_after:.2f}s")
            time.sleep(float(retry_after) + 1)
        else:
            print(f"❌ Discord event create failed ({r.status_code}): {r.text}")
    except Exception as e:
        print(f"❌ Exception during Discord event creation: {e}")

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