-- Convert the remaining naive timestamp columns to timestamptz.
--
-- WHY: the code builds timezone-AWARE UTC datetimes (see _parse_iso_z in
-- bot/commands/schedule_sync.py) and inserts them into naive `timestamp` columns.
-- Postgres resolves that by converting to the session's TimeZone and discarding
-- the offset. On Neon the session is UTC, so the stored values are correct today
-- -- but that correctness rests on a server setting the application never asserts.
-- schedule_sync_settings.tz already defaults to 'America/Denver', so per-guild
-- timezones were clearly planned. The day that ships, these naive columns could
-- silently shift every event by 6-7 hours.
--
-- The USING clause declares that the existing values are UTC. Without it Postgres
-- reinterprets them using the session TimeZone, which can move the data.
--
-- IDEMPOTENT: the DO block only touches columns that are still declared naive, so
-- re-running is a no-op. This matters because a bare ALTER run twice WOULD corrupt
-- data -- `timestamptz AT TIME ZONE 'UTC'` is the inverse conversion.
--
-- APPLY: paste into Neon's SQL editor and run, then confirm with
--     python sql/dump_schema.py
-- and check that 02_live_schema.sql shows 'timestamp with time zone' throughout.

BEGIN;

DO $$
DECLARE
    target    record;
    converted int := 0;
BEGIN
    FOR target IN
        SELECT c.table_name, c.column_name
        FROM information_schema.columns c
        WHERE c.table_schema = 'public'
          AND c.data_type = 'timestamp without time zone'
          AND (c.table_name::text, c.column_name::text) IN (
                ('bot_users',              'premium_expires_at'),
                ('schedule_sync_settings', 'last_sync'),
                ('synced_events',          'start_time'),
                ('synced_events',          'end_time'),
                ('synced_events',          'last_seen_at'),
                ('twitch_tokens',          'updated_at')
          )
        ORDER BY c.table_name, c.column_name
    LOOP
        EXECUTE format(
            'ALTER TABLE public.%I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
            target.table_name, target.column_name, target.column_name
        );
        RAISE NOTICE 'Converted %.% -> timestamptz', target.table_name, target.column_name;
        converted := converted + 1;
    END LOOP;

    IF converted = 0 THEN
        RAISE NOTICE 'Nothing to do: all target columns are already timestamptz.';
    ELSE
        RAISE NOTICE 'Done. Converted % column(s).', converted;
    END IF;
END $$;

COMMIT;
