import os
import asyncio
import hashlib
import datetime as dt
from typing import Dict, Any, List, Optional, Mapping

import aiohttp

TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID")
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
TW_BASE = "https://api.twitch.tv/helix"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Helix allows 800 points/minute per client ID. At first=25 that is only ~32
# pages, so an unbounded paginated read can exhaust the whole budget in seconds.
# This is the hard ceiling used whenever a caller doesn't specify one.
DEFAULT_MAX_PAGES = 4

# How many times to wait-and-retry a single request that came back 429.
MAX_RATE_LIMIT_RETRIES = 3

# Never park a command for longer than this waiting on a bucket refill.
MAX_RATE_LIMIT_WAIT = 60.0


def _rfc3339(dt_obj: dt.datetime) -> str:
    if dt_obj.tzinfo is None:
        dt_obj = dt_obj.replace(tzinfo=dt.timezone.utc)
    return dt_obj.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _rate_limit_delay(headers: Mapping[str, str], attempt: int) -> float:
    """Seconds to wait after a 429, preferring Twitch's own guidance.

    Twitch signals throttling two different ways, so we check both:
      - `Retry-After`: seconds to wait (sent on some endpoints)
      - `Ratelimit-Reset`: a Unix timestamp for when the points bucket refills
    If neither is present or parseable, fall back to exponential backoff.
    """
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return min(max(float(retry_after), 1.0), MAX_RATE_LIMIT_WAIT)
        except ValueError:
            pass

    reset = headers.get("Ratelimit-Reset")
    if reset:
        try:
            # Clamp: a stale or clock-skewed reset value shouldn't stall us for minutes.
            wait = float(reset) - dt.datetime.now(dt.timezone.utc).timestamp()
            return min(max(wait, 1.0), MAX_RATE_LIMIT_WAIT)
        except ValueError:
            pass

    return float(2 ** attempt)  # 1s, 2s, 4s

class TwitchAPI:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        self.session = session

        cid = client_id or TWITCH_CLIENT_ID
        cs = client_secret or TWITCH_CLIENT_SECRET
        if not cid:
            raise RuntimeError("TWITCH_CLIENT_ID is not set")
        if not cs:
            raise RuntimeError("TWITCH_CLIENT_SECRET is not set")

        self.client_id: str = cid
        self.client_secret: str = cs

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
        async with self.session.post(url, params=params, timeout=HTTP_TIMEOUT) as r:
            r.raise_for_status()
            return await r.json()

    async def _get_json(
        self,
        url: str,
        *,
        access_token: str,
        params: Dict[str, str],
        max_retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> Dict[str, Any]:
        """GET a Helix endpoint, waiting out 429s instead of failing on them.

        Previously a 429 raised immediately, which meant the only recovery was the
        user re-running the command — straight back into the same throttled bucket.
        """
        attempt = 0
        while True:
            delay = 0.0

            async with self.session.get(
                url, headers=self._headers(access_token), params=params, timeout=HTTP_TIMEOUT
            ) as r:
                if r.status == 200:
                    return await r.json()

                if r.status == 429 and attempt < max_retries:
                    # Work out the wait here (while the headers are in scope), but
                    # sleep outside the block so the connection returns to the pool.
                    delay = _rate_limit_delay(r.headers, attempt)
                    print(
                        f"⏳ Twitch 429 on {url} "
                        f"(remaining={r.headers.get('Ratelimit-Remaining')}, "
                        f"reset={r.headers.get('Ratelimit-Reset')}); "
                        f"waiting {delay:.1f}s — retry {attempt + 1}/{max_retries}"
                    )
                else:
                    # Either a non-429 error, or we're out of retries. Keep the
                    # rate-limit headers in the message; they make 429s diagnosable.
                    body = await r.text()
                    raise RuntimeError(
                        f"Twitch GET {url} -> {r.status}: {body} "
                        f"[limit={r.headers.get('Ratelimit-Limit')}, "
                        f"remaining={r.headers.get('Ratelimit-Remaining')}, "
                        f"reset={r.headers.get('Ratelimit-Reset')}]"
                    )

            await asyncio.sleep(delay)
            attempt += 1

    async def get_schedule_segments(
        self,
        broadcaster_id: str,
        access_token: str,
        start: Optional[str] = None,
        first: int = 25,
        max_pages: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch schedule segments under a hard page bound.

        /schedule projects *recurring* segments forward, so it can keep handing back
        a pagination cursor with no natural end. Following that cursor unbounded is
        what exhausts the Helix budget and produces the 429s. We stop on the first
        of four conditions:
          1. Twitch returns no cursor (a normal, finite schedule)
          2. A page comes back with zero segments
          3. The cursor repeats — Twitch is handing us the same page again
          4. We reach `max_pages`

        `max_pages=None` means DEFAULT_MAX_PAGES, *not* unlimited. That distinction
        is the actual bug this replaces: the old default of None disabled the cap.
        """
        page_limit = max_pages if max_pages and max_pages > 0 else DEFAULT_MAX_PAGES

        params = {"broadcaster_id": broadcaster_id, "first": str(first)}
        if start:
            params["start_time"] = start

        out: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()       # segments can repeat across pages
        seen_cursors: set[str] = set()   # catches a cursor that loops back on itself
        cursor: Optional[str] = None

        # A bounded for-loop rather than `while True` — condition 4 is now structural
        # and can't be switched off by a bad argument.
        for page in range(1, page_limit + 1):
            if cursor:
                params["after"] = cursor

            print(f"🌐 Schedule call: bcid={broadcaster_id}, page={page}/{page_limit}")
            body = await self._get_json(
                f"{TW_BASE}/schedule", access_token=access_token, params=params
            )

            segments = (body.get("data") or {}).get("segments") or []

            # Condition 2: nothing left to collect, even if a cursor was still sent.
            if not segments:
                break

            for seg in segments:
                seg_id = seg.get("id")
                if seg_id:
                    if seg_id in seen_ids:
                        continue
                    seen_ids.add(seg_id)
                out.append(seg)

            cursor = (body.get("pagination") or {}).get("cursor")

            # Condition 1: end of a finite schedule.
            if not cursor:
                break

            # Condition 3: same cursor twice means we're going in circles.
            if cursor in seen_cursors:
                print(f"⚠️ Twitch returned a repeating pagination cursor; stopping at page {page}.")
                break
            seen_cursors.add(cursor)

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

        async with self.session.post(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, json=json_payload, timeout=HTTP_TIMEOUT) as r:
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

        async with self.session.patch(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, json=json_payload, timeout=HTTP_TIMEOUT) as r:
            if r.status not in (200, 204):
                body = await r.text()
                raise RuntimeError(f"Twitch update segment {r.status}: {body}")
            # 204 has no body
            return (await r.json()) if r.status == 200 else {"ok": True}

    async def delete_segment(self, broadcaster_id: str, access_token: str, segment_id: str) -> None:
        # DELETE /schedule/segment
        params = {"broadcaster_id": broadcaster_id, "id": segment_id}
        async with self.session.delete(f"{TW_BASE}/schedule/segment", headers=self._headers(access_token), params=params, timeout=HTTP_TIMEOUT) as r:
            if r.status not in (200, 204):
                body = await r.text()
                raise RuntimeError(f"Twitch delete segment {r.status}: {body}")
