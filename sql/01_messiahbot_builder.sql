CREATE TABLE IF NOT EXISTS guild_settings (
  guild_id         TEXT PRIMARY KEY,
  tz               TEXT NOT NULL DEFAULT 'America/Denver',
  admin_channel_id TEXT,
  features_json    JSONB NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS builder_layouts (
  guild_id  TEXT NOT NULL,
  version   INTEGER NOT NULL DEFAULT 1,
  payload   JSONB  NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (guild_id, version)
);
