import os, hashlib, datetime as dt
from typing import Dict, Any, List, Optional
import aiohttp

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TW_BASE = "https://api.twitch.tv/helix"

def _rfc3339(dt_obj: dt.datetime) -> str:
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")

class TwitchAPI:
    def __init__(self, session: aiohttp.ClientSession, client_id: Optional[str]=None, client_secret: Optional[str]=None):
        self.session = session
        self.client_id = client_id or TWITCH_CLIENT_ID
        self.client_secret = client_secret or TWITCH_CLIENT_SECRET

    def _h(self, payload: Dict[str, Any]) -> str:
        s = f"{payload.get('title','')}|{payload.get('start')}|{payload.get('end')}|{payload.get('desc','')}|{payload.get('category','')}"
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    def _headers(self, access_token: str) -> Dict[str, str]:
        return {"Client-Id": self.client_id, "Authorization": f"Bearer {access_token}"}

    async def refresh_user_token(self, refresh_token: str) -> Dict[str, Any]:
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        async with self.session.post(url, params=params, timeout=30) as r:
            r.raise_for_status()
            return await r.json()

    async def get_schedule_segments(self, broadcaster_id: str, access_token: str, start: Optional[str]=None, first: int=25) -> List[Dict[str, Any]]:
        params = {"broadcaster_id": broadcaster_id, "first": str(first)}
        if start:
            params["start_time"] = start
        out: List[Dict[str, Any]] = []
        cursor = None
        while True:
            if cursor:
                params["after"] = cursor
            async with self.session.get(f"{TW_BASE}/schedule", headers=self._headers(access_token), params=params, timeout=30) as r:
                if r.status != 200:
                    body = await r.text()
                    raise RuntimeError(f"Twitch schedule GET {r.status}: {body}")
                body = await r.json()
                data = (body.get("data") or {})
                segs = data.get("segments") or []
                out.extend(segs)
                cursor = (body.get("pagination") or {}).get("cursor")
                if not cursor:
                    break
        return out

    async def create_segment(self, broadcaster_id: str, access_token: str, *, title: str, start_time: dt.datetime, duration_mins: int, category_id: Optional[str]=None, is_canceled: bool=False) -> Dict[str, Any]:
        # POST /schedule/segment
        params = {"broadcaster_id": broadcaster_id}
        json_payload: Dict[str, Any] = {
            "title": title,
            "start_time": _rfc3339(start_time),
            "duration": f"{int(duration_mins)}",  # minutes as string
            "is_canceled": bool(is_canceled),
        }
        if category_id:
            json_payload["category_id"] = category_id

        async with self.session.post(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, json=json_payload, timeout=30) as r:
            if r.status not in (200, 201):
                body = await r.text()
                raise RuntimeError(f"Twitch create segment {r.status}: {body}")
            return await r.json()

    async def update_segment(self, broadcaster_id: str, access_token: str, segment_id: str, *, title: Optional[str]=None, start_time: Optional[dt.datetime]=None, duration_mins: Optional[int]=None, category_id: Optional[str]=None, is_canceled: Optional[bool]=None) -> Dict[str, Any]:
        # PATCH /schedule/segment
        params = {"broadcaster_id": broadcaster_id, "id": segment_id}
        json_payload: Dict[str, Any] = {}
        if title is not None:
            json_payload["title"] = title
        if start_time is not None:
            json_payload["start_time"] = _rfc3339(start_time)
        if duration_mins is not None:
            json_payload["duration"] = f"{int(duration_mins)}"
        if category_id is not None:
            json_payload["category_id"] = category_id
        if is_canceled is not None:
            json_payload["is_canceled"] = bool(is_canceled)

        async with self.session.patch(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, json=json_payload, timeout=30) as r:
            if r.status not in (200, 204):
                body = await r.text()
                raise RuntimeError(f"Twitch update segment {r.status}: {body}")
            # 204 has no body
            return (await r.json()) if r.status == 200 else {"ok": True}

    async def delete_segment(self, broadcaster_id: str, access_token: str, segment_id: str) -> None:
        # DELETE /schedule/segment
        params = {"broadcaster_id": broadcaster_id, "id": segment_id}
        async with self.session.delete(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, timeout=30) as r:
            if r.status not in (200, 204):
                body = await r.text()
                raise RuntimeError(f"Twitch delete segment {r.status}: {body}")
