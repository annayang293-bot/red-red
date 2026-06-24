-- ============================================================
-- 0015_topics_drop_single_active.sql
-- Phase 3-3a (model B): drop the global single-active-topic constraint.
--
-- 0001 created `CREATE UNIQUE INDEX uq_topics_one_active ON topics (status) WHERE status='active'`,
-- which enforces AT MOST ONE active topic in the WHOLE table — a single-tenant assumption. Model B
-- wants each workspace to hold multiple live topics, so a second workspace adding a topic (status
-- 'active') would violate it. Drop it.
--
-- After this, `status` is largely vestigial: new per-workspace topics are created as 'active' and
-- coexist; "which topic am I viewing" becomes per-workspace UI state, not a DB-enforced singleton.
-- NON-REGRESSION: the daily cron still reads its topic by (keyword, status='active') — Anna's
-- «AI 创业» stays active, so that path is unchanged. `switch_active_topic` still works (it just no
-- longer has the uniqueness backstop); the new /api/topics will INSERT topics instead of calling it.
-- ============================================================

DROP INDEX IF EXISTS uq_topics_one_active;
