# bot/sync_worker.py
import os
import time
import requests
import psycopg
from datetime import datetime, timezone, timedelta

DATABASE_URL = os.getenv("DATABASE_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

TWITCH_API = "https://api.twitch.tv/helix"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

def refresh_twitch_token(refresh_token):
    """Exchange a refresh token for a new access token."""
    data = {
        "client_id": TWITCH_CLIENT_ID,
        "client_secret": TWITCH_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    r = requests.post(TWITCH_TOKEN_URL, data=data, timeout=20)
    if r.status_code != 200:
        print(f"❌ Twitch refresh failed: {r.status_code} {r.text}")
        return None
    j = r.json()
    return {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token", refresh_token),
        "expires_in": j.get("expires_in", 0)
    }

def update_token_in_db(conn, twitch_user_id, access_token, refresh_token, expires_in):
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE twitch_tokens
            SET access_token=%s,
                refresh_token=%s,
                expires_at=%s,
                updated_at=NOW()
            WHERE twitch_user_id=%s
        """, (access_token, refresh_token, expires_at, twitch_user_id))
        conn.commit()

def fetch_twitch_schedule(access_token, user_id):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": TWITCH_CLIENT_ID,
    }
    r = requests.get(f"{TWITCH_API}/schedule?broadcaster_id={user_id}", headers=headers, timeout=20)
    return r

def run_sync():
    """Main sync loop body."""
    with psycopg.connect(DATABASE_URL, sslmode="require", autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM twitch_tokens")
            tokens = cur.fetchall()
            if not tokens:
                print("⚠️ No Twitch tokens found in DB.")
                return

            for row in tokens:
                twitch_user_id = row[1] if "twitch_user_id" in row else row["twitch_user_id"]
                guild_id = row[0] if "guild_id" in row else row["guild_id"]
                access_token = row["access_token"]
                refresh_token = row["refresh_token"]

                print(f"🔄 Syncing Twitch schedule for guild {guild_id} (user {twitch_user_id})")

                r = fetch_twitch_schedule(access_token, twitch_user_id)

                # Handle expired/invalid token
                if r.status_code == 401:
                    print("🔁 Access token expired — refreshing...")
                    new_tok = refresh_twitch_token(refresh_token)
                    if new_tok:
                        update_token_in_db(conn, twitch_user_id, new_tok["access_token"], new_tok["refresh_token"], new_tok["expires_in"])
                        access_token = new_tok["access_token"]
                        r = fetch_twitch_schedule(access_token, twitch_user_id)
                    else:
                        print(f"❌ Could not refresh token for {twitch_user_id}")
                        continue

                if r.status_code != 200:
                    print(f"❌ Twitch API error {r.status_code}: {r.text}")
                    continue

                data = r.json()
                segments = data.get("data", {}).get("segments", [])
                print(f"✅ Synced {len(segments)} Twitch events for guild {guild_id}")

                # (optional) You could now push these to Discord scheduled events

def main_loop():
    while True:
        print("⏰ Running Twitch→Discord schedule sync...")
        try:
            run_sync()
        except Exception as e:
            print(f"❌ Sync run failed: {e}")
        print("😴 Sleeping 1 hour...")
        time.sleep(3600)

if __name__ == "__main__":
    main_loop()