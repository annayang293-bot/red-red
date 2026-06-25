-- ============================================================
-- 0017_backfill_null_workspace_runs.sql
-- Phase 3-7: backfill the NULL-workspace daily-cron runs to Anna's workspace.
--
-- WHY: before Phase 3-4/3-7, the daily cron dispatched «AI 创业» with NO workspace_id, so its runs
-- (#87..95 and any produced by the OLD live cron up until the gated build is deployed) landed with
-- runs.workspace_id = NULL. The Phase 3-5 scoped report reads filter by workspace_id, so those runs
-- would be INVISIBLE to Anna in the new (gated) UI. This stamps them onto her workspace.
--
-- SCOPE: only «AI 创业» (her only auto_daily topic) runs that are still NULL. Other workspaces never
-- produce NULL-workspace runs (they go through the gated /api/run or the new cron, both of which
-- stamp workspace_id). The runs' topic_id already points at her «AI 创业» topic (0014 backfilled
-- that topic to her workspace), so this just aligns runs.workspace_id with its topic's workspace.
--
-- SAFETY: idempotent (re-running matches nothing once stamped). Harmless on a fresh DB (no rows).
-- No live impact on the CURRENT deployed (un-gated) app — it reads runs globally, so adding a
-- workspace_id changes nothing it shows. Run this any time before deploying the gated build.
-- (Anna runs migrations manually in the Supabase SQL Editor.)
-- ============================================================

UPDATE runs
SET workspace_id = 'b6f4fe6a-d989-4e52-abb0-f1c3f5d28b5f'
WHERE workspace_id IS NULL
  AND topic_keyword = 'AI 创业';
