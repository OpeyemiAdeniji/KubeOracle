-- Run this in the InsForge SQL editor (Dashboard → SQL Editor)
-- Creates the saved_queries table for persisted NL query history.

CREATE TABLE IF NOT EXISTS saved_queries (
  id          bigserial     PRIMARY KEY,
  user_id     text          NOT NULL,
  question    text          NOT NULL,
  answer      text          NOT NULL DEFAULT '',
  kubectl_commands jsonb    NOT NULL DEFAULT '[]',
  domains_checked  jsonb    NOT NULL DEFAULT '[]',
  confidence  integer       NOT NULL DEFAULT 0,
  severity    text          NOT NULL DEFAULT 'low',
  context     text          NOT NULL DEFAULT '',
  created_at  timestamptz   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS saved_queries_user_id_idx
  ON saved_queries (user_id, created_at DESC);
