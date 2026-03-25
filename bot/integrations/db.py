# bot/db.py
"""
Shared database utilities (Neon/Postgres) for MessiahBot.

Goals:
- Create ONE async connection pool per process
- Provide small helpers for querying with dict-like rows
"""

# bot/db.py
import os
from typing import Any, Iterable, Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

_pool: Optional[AsyncConnectionPool] = None


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
    )
    await _pool.open()
    return _pool


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized. Call init_db_pool() at startup.")
    return _pool


async def fetch_one(sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
    async with pool().connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, tuple(params))
            return await cur.fetchone()