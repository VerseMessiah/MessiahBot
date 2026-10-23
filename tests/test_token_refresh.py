"""Tests for get_valid_access_token — the Twitch token refresh fixes.

Four problems these cover:
  1. The stored lifetime was hardcoded to 60 minutes, discarding the expires_in
     Twitch reports (~4h), causing roughly 4x more refreshes than necessary.
  2. Twitch rotates refresh tokens, so a refresh kills the old one. If saving the
     new one failed, the stored token was dead with nothing to signal it — the
     only recovery being a manual re-run of OAuth.
  3. The UPDATE was scoped by guild_id alone, so refreshing one linked Twitch
     account would overwrite another's row.
  4. The SELECT had no ORDER BY, so with multiple rows Postgres could return a
     different one between runs.

Run:  python tests/test_token_refresh.py
"""

import asyncio
import datetime as dt

from _support import Suite  # noqa: F401  (also fixes sys.path)

import bot.commands.schedule_sync as ss


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class FakeTwitchAPI:
    """Stands in for TwitchAPI, recording whether a refresh was attempted."""

    def __init__(self, session, response=None):
        self.refreshed = False
        self._response = response

    async def refresh_user_token(self, refresh_token):
        self.refreshed = True
        return self._response or {
            "access_token": "NEW_AT",
            "refresh_token": "NEW_RT",
            "expires_in": 14400,  # what Twitch actually sends: 4 hours
        }


def run_token_flow(row, api_response=None, failing_saves=0):
    """Call get_valid_access_token with the DB and Twitch replaced by fakes.

    Returns (result, error, calls, api) where `calls` records the SQL that ran.
    """
    calls = {"select": [], "update": [], "save_attempts": 0}

    async def fake_fetch_one(sql, params=()):
        calls["select"].append(" ".join(sql.split()))
        return row

    async def fake_execute(sql, params=()):
        calls["save_attempts"] += 1
        if calls["save_attempts"] <= failing_saves:
            # The exact wording db.py classifies as transient.
            raise RuntimeError("ssl connection has been closed unexpectedly")
        calls["update"].append((" ".join(sql.split()), params))
        return 1

    holder = {}

    def fake_api(session):
        holder["api"] = FakeTwitchAPI(session, api_response)
        return holder["api"]

    original = (ss.fetch_one, ss.execute, ss.TwitchAPI)
    ss.fetch_one, ss.execute, ss.TwitchAPI = fake_fetch_one, fake_execute, fake_api
    try:
        try:
            return asyncio.run(ss.get_valid_access_token(None, "G1")), None, calls, holder.get("api")
        except Exception as e:  # noqa: BLE001 - the test decides if raising is correct
            return None, e, calls, holder.get("api")
    finally:
        ss.fetch_one, ss.execute, ss.TwitchAPI = original


BASE_ROW = {
    "twitch_user_id": "B1",
    "access_token": "OLD_AT",
    "refresh_token": "OLD_RT",
    "expires_at": None,  # each test overrides this
}


def row_with(expires_at) -> dict:
    return dict(BASE_ROW, expires_at=expires_at)


def main() -> bool:
    s = Suite("get_valid_access_token (Twitch token refresh)")

    # --- When to refresh ----------------------------------------------------

    res, err, calls, api = run_token_flow(row_with(now() + dt.timedelta(hours=3)))
    s.check(
        "valid token is reused without refreshing",
        res == ("B1", "OLD_AT") and api is None and not calls["update"],
        f"result={res}",
    )

    res, err, calls, api = run_token_flow(row_with(now() + dt.timedelta(minutes=2)))
    s.check(
        "token inside the 5-minute margin is refreshed",
        res == ("B1", "NEW_AT") and api is not None and api.refreshed,
        f"result={res}",
    )

    # A plain TIMESTAMP column yields a naive datetime; comparing naive to aware
    # raises TypeError. _as_aware_utc makes this work either way.
    res, err, calls, api = run_token_flow(
        row_with((now() + dt.timedelta(hours=3)).replace(tzinfo=None))
    )
    s.check(
        "naive timestamp does not raise TypeError",
        err is None and res == ("B1", "OLD_AT"),
        f"error={err}",
    )

    res, err, calls, api = run_token_flow(row_with(None))
    s.check(
        "NULL expiry is treated as expired",
        err is None and api is not None and api.refreshed,
        f"error={err}",
    )

    # --- What gets written --------------------------------------------------

    res, err, calls, api = run_token_flow(row_with(now() + dt.timedelta(minutes=2)))
    saved_sql, saved_params = calls["update"][0]

    s.check(
        "stored lifetime comes from Twitch's expires_in",
        saved_params[2] == 14400,
        f"ttl={saved_params[2]}s (not a hardcoded 3600)",
    )
    s.check(
        "UPDATE is scoped to guild_id AND twitch_user_id",
        "WHERE guild_id = %s AND twitch_user_id = %s" in saved_sql,
    )
    s.check(
        "SELECT is deterministic",
        "ORDER BY updated_at DESC NULLS LAST LIMIT 1" in calls["select"][0],
    )

    res, err, calls, api = run_token_flow(
        row_with(now() + dt.timedelta(minutes=2)),
        api_response={"access_token": "A", "refresh_token": "R"},  # no expires_in
    )
    s.check(
        "missing expires_in falls back safely",
        err is None and calls["update"][0][1][2] == ss.FALLBACK_TOKEN_TTL_SECONDS,
        f"ttl={calls['update'][0][1][2]}s",
    )

    # --- Surviving a failed save (the lockout risk) -------------------------

    res, err, calls, api = run_token_flow(
        row_with(now() + dt.timedelta(minutes=2)), failing_saves=2
    )
    s.check(
        "transient save failures are retried",
        err is None and res == ("B1", "NEW_AT") and calls["save_attempts"] == 3,
        f"attempts={calls['save_attempts']}",
    )

    res, err, calls, api = run_token_flow(
        row_with(now() + dt.timedelta(minutes=2)), failing_saves=99
    )
    s.check(
        "unsaveable token raises instead of being returned",
        res is None and isinstance(err, RuntimeError),
        f"attempts={calls['save_attempts']}",
    )
    s.check(
        "that error tells the user how to recover",
        err is not None and "Re-link Twitch" in str(err),
    )

    # --- Missing connection -------------------------------------------------

    res, err, calls, api = run_token_flow(None)
    s.check(
        "no linked account gives a clear ValueError",
        isinstance(err, ValueError),
        f"raised={type(err).__name__}",
    )

    return s.summary()


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)
