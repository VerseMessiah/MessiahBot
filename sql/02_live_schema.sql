-- MessiahBot live database schema
-- Generated 2026-10-22 20:38:11 UTC by sql/dump_schema.py
-- Server: PostgreSQL 17.11 (11592b3)
--
-- STRUCTURE ONLY — contains no row data, no tokens, no secrets.
-- Regenerate with:  python sql/dump_schema.py

CREATE TABLE IF NOT EXISTS public.app_kv (
    key text NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.app_kv
    ADD CONSTRAINT app_kv_pkey PRIMARY KEY (key);

CREATE TABLE IF NOT EXISTS public.bot_users (
    id integer DEFAULT nextval('bot_users_id_seq'::regclass) NOT NULL,
    discord_id text,
    twitch_id text,
    premium boolean DEFAULT false,
    premium_expires_at timestamp without time zone
);

ALTER TABLE public.bot_users
    ADD CONSTRAINT bot_users_pkey PRIMARY KEY (id);

ALTER TABLE public.bot_users
    ADD CONSTRAINT bot_users_discord_id_key UNIQUE (discord_id);

ALTER TABLE public.bot_users
    ADD CONSTRAINT bot_users_twitch_id_key UNIQUE (twitch_id);

CREATE TABLE IF NOT EXISTS public.builder_layouts (
    guild_id text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    type text DEFAULT 'snapshot'::text NOT NULL,
    layout_type text DEFAULT 'active'::text
);

ALTER TABLE public.builder_layouts
    ADD CONSTRAINT builder_layouts_pkey PRIMARY KEY (guild_id, version);

CREATE TABLE IF NOT EXISTS public.guild_settings (
    guild_id text NOT NULL,
    tz text DEFAULT 'America/Denver'::text NOT NULL,
    admin_channel_id text,
    features_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE public.guild_settings
    ADD CONSTRAINT guild_settings_pkey PRIMARY KEY (guild_id);

CREATE TABLE IF NOT EXISTS public.schedule_sync_settings (
    guild_id text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    tz text DEFAULT 'America/Denver'::text NOT NULL,
    default_channel_id text,
    category_map_json jsonb,
    last_error text,
    premium_sync boolean DEFAULT false,
    user_id integer,
    twitch_id text,
    last_sync timestamp without time zone
);

ALTER TABLE public.schedule_sync_settings
    ADD CONSTRAINT schedule_sync_settings_pkey PRIMARY KEY (guild_id);

ALTER TABLE public.schedule_sync_settings
    ADD CONSTRAINT schedule_sync_settings_user_id_fkey FOREIGN KEY (user_id) REFERENCES bot_users(id);

CREATE TABLE IF NOT EXISTS public.synced_events (
    id integer DEFAULT nextval('synced_events_id_seq'::regclass) NOT NULL,
    external_id text,
    origin text NOT NULL,
    guild_id text,
    user_id integer,
    title text,
    description text,
    start_time timestamp without time zone,
    end_time timestamp without time zone,
    location text,
    last_seen_at timestamp without time zone,
    last_sync_source text,
    deleted boolean DEFAULT false
);

ALTER TABLE public.synced_events
    ADD CONSTRAINT synced_events_pkey PRIMARY KEY (id);

ALTER TABLE public.synced_events
    ADD CONSTRAINT synced_events_unique UNIQUE (external_id, guild_id);

ALTER TABLE public.synced_events
    ADD CONSTRAINT synced_events_user_id_fkey FOREIGN KEY (user_id) REFERENCES bot_users(id);

CREATE TABLE IF NOT EXISTS public.twitch_tokens (
    id integer DEFAULT nextval('twitch_tokens_id_seq'::regclass) NOT NULL,
    guild_id text NOT NULL,
    twitch_user_id text NOT NULL,
    access_token text NOT NULL,
    refresh_token text NOT NULL,
    expires_at timestamp with time zone,
    scope text,
    updated_at timestamp without time zone DEFAULT now()
);

ALTER TABLE public.twitch_tokens
    ADD CONSTRAINT twitch_tokens_pkey PRIMARY KEY (id);

ALTER TABLE public.twitch_tokens
    ADD CONSTRAINT twitch_tokens_unique UNIQUE (guild_id, twitch_user_id);
