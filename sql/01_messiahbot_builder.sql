-- Placeholder DDL for builder layouts table
CREATE TABLE IF NOT EXISTS builder_layouts (
  guild_id TEXT NOT NULL,
  version INT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMP DEFAULT NOW(),
  PRIMARY KEY (guild_id, version)
);
