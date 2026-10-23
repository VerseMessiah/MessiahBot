#!/usr/bin/env python
"""Dump the live database's STRUCTURE (never its data) to a replayable .sql file.

Why this exists: most of MessiahBot's tables (twitch_tokens, synced_events,
bot_users) only existed inside the live Neon database. Nothing in the repo
recorded their columns, types, or — critically — the UNIQUE indexes that every
`ON CONFLICT` upsert in the codebase depends on. Lose the database and you'd be
reverse-engineering all of that from INSERT statements.

This reads from pg_catalog rather than shelling out to pg_dump, so it needs no
Postgres client tools installed — just psycopg, which is already a dependency.

Usage (from the repo root):
    python sql/dump_schema.py                      # writes sql/02_live_schema.sql
    python sql/dump_schema.py --out other.sql      # custom destination
    python sql/dump_schema.py --print              # stdout only, write nothing

DATABASE_URL is read from the environment, or from a .env file in the repo root.

SAFETY: this only ever queries system catalogs, never your tables. No access
tokens or refresh tokens can end up in the output. It is safe to commit.
"""

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "sql" / "02_live_schema.sql"

# Postgres marks a constraint's type with a single char; we only name the ones
# worth sorting into a sensible emit order.
CONSTRAINT_ORDER = {"p": 0, "u": 1, "f": 2, "c": 3}  # primary, unique, foreign, check


def _load_database_url() -> str:
    """Find DATABASE_URL in the environment, falling back to a .env file.

    Deliberately minimal: we don't want to import python-dotenv just for this,
    and we never print the value.
    """
    url = os.getenv("DATABASE_URL")
    if url:
        return url

    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "DATABASE_URL":
                return value.strip().strip('"').strip("'")

    sys.exit(
        "❌ DATABASE_URL not found.\n"
        "   Copy it from Render > Environment Group 'messiahbot-core', then either:\n"
        "     - put it in a .env file at the repo root (already gitignored), or\n"
        "     - set it for one command:  DATABASE_URL='...' python sql/dump_schema.py"
    )


# ----------------------------------------------------------------------------
#   CATALOG QUERIES
#   format_type() and pg_get_constraintdef() ask Postgres to render the exact
#   declaration itself, which is far more reliable than rebuilding types by hand
#   from information_schema.
# ----------------------------------------------------------------------------

COLUMNS_SQL = """
SELECT c.relname                                AS table_name,
       a.attname                                AS column_name,
       format_type(a.atttypid, a.atttypmod)     AS col_type,
       a.attnotnull                             AS not_null,
       pg_get_expr(d.adbin, d.adrelid)          AS col_default
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
WHERE n.nspname = 'public'
  AND c.relkind = 'r'          -- ordinary tables only (no views, no sequences)
  AND a.attnum > 0             -- skip system columns like ctid
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

CONSTRAINTS_SQL = """
SELECT c.relname                          AS table_name,
       con.conname                        AS name,
       con.contype                        AS kind,
       pg_get_constraintdef(con.oid)      AS definition
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
ORDER BY c.relname, con.conname
"""

# Indexes that exist on their own rather than backing a constraint. We filter the
# constraint-backed ones out in Python, since those are emitted as constraints.
INDEXES_SQL = """
SELECT tablename AS table_name, indexname AS name, indexdef AS definition
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname
"""


def _render(columns, constraints, indexes, db_version: str) -> str:
    tables: dict[str, list[dict]] = {}
    for row in columns:
        tables.setdefault(row["table_name"], []).append(row)

    by_table_constraints: dict[str, list[dict]] = {}
    constraint_names: set[str] = set()
    for row in constraints:
        by_table_constraints.setdefault(row["table_name"], []).append(row)
        constraint_names.add(row["name"])

    out: list[str] = [
        "-- MessiahBot live database schema",
        f"-- Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
        " by sql/dump_schema.py",
        f"-- Server: {db_version}",
        "--",
        "-- STRUCTURE ONLY — contains no row data, no tokens, no secrets.",
        "-- Regenerate with:  python sql/dump_schema.py",
        "",
    ]

    for table in sorted(tables):
        cols = tables[table]
        out.append(f"CREATE TABLE IF NOT EXISTS public.{table} (")
        rendered = []
        for col in cols:
            piece = f"    {col['column_name']} {col['col_type']}"
            if col["col_default"] is not None:
                piece += f" DEFAULT {col['col_default']}"
            if col["not_null"]:
                piece += " NOT NULL"
            rendered.append(piece)
        out.append(",\n".join(rendered))
        out.append(");")
        out.append("")

        # Constraints come after the table so ordering between tables (e.g. a
        # foreign key pointing at a table defined later in the file) can't fail.
        for con in sorted(
            by_table_constraints.get(table, []),
            key=lambda c: (CONSTRAINT_ORDER.get(c["kind"], 9), c["name"]),
        ):
            out.append(
                f"ALTER TABLE public.{table}\n"
                f"    ADD CONSTRAINT {con['name']} {con['definition']};"
            )
            out.append("")

    standalone = [i for i in indexes if i["name"] not in constraint_names]
    if standalone:
        out.append("-- Indexes not backed by a constraint")
        for idx in standalone:
            out.append(f"{idx['definition']};")
        out.append("")

    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="destination .sql file")
    parser.add_argument(
        "--print", dest="to_stdout", action="store_true", help="print instead of writing"
    )
    args = parser.parse_args()

    url = _load_database_url()

    # autocommit + read-only catalog queries: nothing here can modify the database.
    with psycopg.connect(url, sslmode="require", autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT version() AS v")
            version_row = cur.fetchone() or {}
            db_version = str(version_row.get("v", "unknown")).split(" on ")[0]

            cur.execute(COLUMNS_SQL)
            columns = cur.fetchall()
            cur.execute(CONSTRAINTS_SQL)
            constraints = cur.fetchall()
            cur.execute(INDEXES_SQL)
            indexes = cur.fetchall()

    if not columns:
        sys.exit("❌ No tables found in schema 'public'. Wrong database?")

    sql_text = _render(columns, constraints, indexes, db_version)

    if args.to_stdout:
        print(sql_text)
        return

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(sql_text, encoding="utf-8")

    table_names = sorted({c["table_name"] for c in columns})
    print(f"✅ Wrote {dest} ({len(table_names)} tables, {len(constraints)} constraints)")
    for name in table_names:
        print(f"   - {name}")

    # Answers the open question of whether expires_at is TIMESTAMPTZ, which decides
    # if the token comparison in schedule_sync.py is safe.
    for col in columns:
        if col["table_name"] == "twitch_tokens" and col["column_name"] == "expires_at":
            print(f"\nℹ️ twitch_tokens.expires_at is: {col['col_type']}")


if __name__ == "__main__":
    main()
