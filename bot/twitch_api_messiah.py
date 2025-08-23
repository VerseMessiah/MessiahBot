# bot/twitch_api_messiah.py
import os
import aiohttp
from dotenv import load_dotenv

load_dotenv()
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")

class TwitchAPI:
    """Minimal placeholder Twitch API wrapper (extend later)."""

    def __init__(self, access_token: str | None = None, user_id: str | None = None):
        self.access_token = access_token
        self.user_id = user_id

    async def fetch_schedule_segments(self) -> list:
        """Return upcoming schedule segments for self.user_id (requires access_token)."""
        if not self.access_token or not self.user_id:
            return []
        url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={self.user_id}"
        headers = {
            "Client-ID": TWITCH_CLIENT_ID or "",
            "Authorization": f"Bearer {self.access_token}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return (data.get("data") or {}).get("segments", []) or []
