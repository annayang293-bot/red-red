-- ============================================================
-- 0016_topics_workspace_keyword_unique.sql
-- Phase 3 (model B): one live topic row per (keyword, workspace).
--
-- After 0015 dropped the global single-active index, nothing stopped two rows with the same
-- (keyword, workspace_id) — e.g. a concurrent ensure_topic INSERT race, or the topics API + a run
-- both creating one. This partial unique index enforces the correct model-B invariant and lets the
-- topics API upsert on (keyword, workspace_id). WHERE workspace_id IS NOT NULL so legacy NULL-
-- workspace rows (pre-backfill / cron-created) are exempt.
-- ============================================================

CREATE UNIQUE INDEX uq_topics_workspace_keyword
  ON topics (keyword, workspace_id)
  WHERE workspace_id IS NOT NULL;
