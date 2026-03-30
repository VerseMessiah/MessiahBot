# bot/integrations/db.py
"""Shared database utilities (Neon/Postgres) for MessiahBot.

Goals:
- Create ONE async connection pool per process
- Provide small helpers for querying with dict-like rows
"""

import os
import asyncio
from typing import Any, Iterable, Optional, cast
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


_pool: Optional[AsyncConnectionPool] = None

def _is_transient_db_error(e: Exception) -> bool:
    msg = str(e).lower()
    needles = [
        "ssl connection has been closed unexpectedly",
        "server closed the connection unexpectedly",
        "connection is closed",
        "connection reset by peer",
        "broken pipe",
        "terminating connection",
    ]
    return any(n in msg for n in needles)


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


async def init_db_pool() -> AsyncConnectionPool:
    """Create the async pool once per process. Safe to call multiple times."""
    global _pool
    if _pool is not None:
        return _pool

    _pool = AsyncConnectionPool(
        conninfo=_db_url(),
        min_size=1,
        max_size=5,
        kwargs={"sslmode": "require"},
        open=False,
    )
    await _pool.open()
    return _pool


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_db_pool() at startup.")
    return _pool


async def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    """Run a SELECT that returns a single row (or None)."""
    for attempt in range(2):
        try:
            async with pool().connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(cast(Any, sql), tuple(params))
                    return await cur.fetchone()
        except Exception as e:
            if attempt == 0 and _is_transient_db_error(e):
                await asyncio.sleep(0.5)
                continue
            raise


async def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict]: # pyright: ignore[reportReturnType]
    """Run a SELECT that returns multiple rows (possibly empty)."""
    for attempt in range(2):
        try:
            async with pool().connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(cast(Any, sql), tuple(params))
                    rows = await cur.fetchall()
                    return list(rows or [])
        except Exception as e:
            if attempt == 0 and _is_transient_db_error(e):
                await asyncio.sleep(0.5)
                continue
            raise


async def execute(sql: str, params: Iterable[Any] = ()) -> int: # pyright: ignore[reportReturnType]
    """Run an INSERT/UPDATE/DELETE. Returns the cursor rowcount when available."""
    for attempt in range(2):
        try:
            async with pool().connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute(cast(Any, sql), tuple(params))
                    return cur.rowcount
        except Exception as e:
            if attempt == 0 and _is_transient_db_error(e):
                await asyncio.sleep(0.5)
                continue
            raise