"""Tests for TwitchAPI.get_schedule_segments — the Twitch 429 fix.

The bug: /schedule projects *recurring* segments forward, so it can keep handing
back a pagination cursor with no natural end. The old loop's only exit was
`if not cursor: break`, and its `max_pages` guard defaulted to None — which made
`if max_pages and ...` falsy, silently disabling the cap. Result: unbounded
requests, Helix's 800-points/minute budget gone in seconds, HTTP 429.

These tests script Twitch's responses so that behaviour is reproducible in
milliseconds, with no network, no credentials and no real rate limiting.

Run:  python tests/test_twitch_api.py
"""

import asyncio

from _support import Suite  # noqa: F401  (also fixes sys.path)

from bot.integrations.twitch_api import DEFAULT_MAX_PAGES, TwitchAPI


class FakeResponse:
    """Stands in for an aiohttp response.

    Implements only what the code under test touches: .status, .headers, .json(),
    .text(), and the async context-manager protocol behind `async with`.
    """

    def __init__(self, status: int, payload=None, headers=None):
        self.status = status
        self._payload = payload or {}
        self.headers = headers or {}

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Stands in for aiohttp.ClientSession, driven by a scripted responder.

    This substitution is only possible because TwitchAPI *receives* its session
    rather than constructing one internally (dependency injection).
    """

    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        # Snapshot the params: the code under test mutates one dict in place
        # (params["after"] = cursor), so storing the reference would make every
        # recorded call show the final cursor.
        self.calls.append(dict(params or {}))
        return self.responder(len(self.calls) - 1, dict(params or {}))


def segment(i: int) -> dict:
    return {"id": f"seg{i}", "title": f"Stream {i}"}


def page(segments, cursor=None) -> dict:
    body = {"data": {"segments": segments}}
    if cursor:
        body["pagination"] = {"cursor": cursor}
    return body


def fetch(responder, **kwargs):
    """Run get_schedule_segments against a scripted Twitch; return (session, result, error)."""
    session = FakeSession(responder)
    api = TwitchAPI(session)  # type: ignore[arg-type]
    try:
        return session, asyncio.run(api.get_schedule_segments("12345", "tok", **kwargs)), None
    except Exception as e:  # noqa: BLE001 - the test decides whether raising is correct
        return session, None, e


def main() -> bool:
    s = Suite("TwitchAPI.get_schedule_segments (Twitch 429 fix)")

    # --- Stopping conditions -------------------------------------------------

    # THE ORIGINAL BUG: a recurring schedule returns a cursor forever.
    sess, out, err = fetch(lambda i, p: FakeResponse(200, page([segment(i)], f"c{i}")))
    s.check(
        "infinite cursor stops at default cap",
        err is None and len(sess.calls) == DEFAULT_MAX_PAGES,
        f"requests={len(sess.calls)}",
    )

    sess, out, err = fetch(
        lambda i, p: FakeResponse(200, page([segment(i)], f"c{i}")), max_pages=1
    )
    s.check("max_pages=1 is respected", len(sess.calls) == 1, f"requests={len(sess.calls)}")

    # max_pages=None must mean "use the default", NOT "unlimited". This is the
    # exact inversion that caused the outage.
    sess, out, err = fetch(
        lambda i, p: FakeResponse(200, page([segment(i)], f"c{i}")), max_pages=None
    )
    s.check(
        "max_pages=None means default, not unlimited",
        len(sess.calls) == DEFAULT_MAX_PAGES,
        f"requests={len(sess.calls)}",
    )

    sess, out, err = fetch(lambda i, p: FakeResponse(200, page([segment(i)], "SAME-CURSOR")))
    s.check("repeating cursor stops early", len(sess.calls) == 2, f"requests={len(sess.calls)}")

    # An empty page with a cursor still attached: the old code kept going.
    sess, out, err = fetch(
        lambda i, p: FakeResponse(200, page([] if i else [segment(0)], f"c{i}"))
    )
    s.check(
        "empty page stops even with a cursor",
        len(sess.calls) == 2 and out is not None and len(out) == 1,
        f"requests={len(sess.calls)} segments={len(out or [])}",
    )

    def finite(i, p):
        return FakeResponse(200, page([segment(i * 2), segment(i * 2 + 1)], "next" if i < 1 else None))

    sess, out, err = fetch(finite)
    s.check(
        "finite schedule stops when cursor runs out",
        len(sess.calls) == 2 and len(out or []) == 4,
        f"requests={len(sess.calls)} segments={len(out or [])}",
    )

    # --- Correctness of what comes back -------------------------------------

    def duplicates(i, p):
        return FakeResponse(200, page([segment(0), segment(1)], f"c{i}" if i < 2 else None))

    sess, out, err = fetch(duplicates)
    s.check(
        "duplicate segment ids are collapsed",
        len(out or []) == 2,
        f"segments={len(out or [])} over {len(sess.calls)} pages",
    )

    sess, out, err = fetch(finite)
    s.check(
        "cursor is threaded into the 'after' param",
        "after" not in sess.calls[0] and sess.calls[1].get("after") == "next",
        f"after={[c.get('after') for c in sess.calls]}",
    )

    # --- Rate-limit handling ------------------------------------------------

    def throttle_once(i, p):
        if i == 0:
            return FakeResponse(429, {"error": "Too Many Requests"}, {"Retry-After": "1"})
        return FakeResponse(200, page([segment(0)]))

    sess, out, err = fetch(throttle_once)
    s.check(
        "429 then 200 recovers instead of failing",
        err is None and len(out or []) == 1,
        f"requests={len(sess.calls)}",
    )

    sess, out, err = fetch(
        lambda i, p: FakeResponse(429, {"error": "Too Many Requests"}, {"Retry-After": "1"})
    )
    s.check(
        "persistent 429 gives up with a clear error",
        isinstance(err, RuntimeError) and "429" in str(err),
        f"raised={type(err).__name__}",
    )

    # Rate-limit headers must survive into the message; they're what makes a 429
    # diagnosable after the fact.
    s.check(
        "error message keeps rate-limit headers",
        err is not None and "remaining=" in str(err) and "reset=" in str(err),
    )

    sess, out, err = fetch(lambda i, p: FakeResponse(401, {"error": "Unauthorized"}))
    s.check(
        "non-429 errors are not retried",
        isinstance(err, RuntimeError) and len(sess.calls) == 1,
        f"requests={len(sess.calls)}",
    )

    return s.summary()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
