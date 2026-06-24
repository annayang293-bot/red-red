-- ============================================================
-- 0014_topics_workspace.sql
-- Phase 3-A (model B): make `topics` per-workspace + add the `auto_daily` opt-in toggle.
--
-- This step is ADDITIVE and NON-BREAKING:
--   * adds workspace_id + auto_daily, backfills existing rows to Anna's workspace, enables RLS.
--   * keeps `status` / `switch_active_topic()` and the existing daily cron working untouched.
-- Later Phase 3 steps move report reads + the cron to the per-(workspace, topic) model and retire
-- the single-global-active-topic concept. The service-role key (used by run_once / the existing
-- /api/topics) bypasses RLS, so enabling RLS here does not break current System ①.
--
-- NOTE: no UNIQUE(workspace_id, keyword) yet — legacy data may hold archived duplicates of the same
-- keyword (the old switch_active_topic re-enabled archived rows). The new topics API will create
-- one live row per (workspace, keyword); a unique constraint can be added after a dedup pass.
-- ============================================================

ALTER TABLE topics ADD COLUMN workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE topics ADD COLUMN auto_daily BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX idx_topics_workspace ON topics (workspace_id);

-- RLS: a workspace member can read/manage that workspace's topics. (Backfill of workspace_id for
-- legacy rows is done in the apply step, parameterized with Anna's workspace id.)
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;

CREATE POLICY topics_select ON topics
  FOR SELECT USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY topics_insert ON topics
  FOR INSERT WITH CHECK (public.is_workspace_member(workspace_id));
CREATE POLICY topics_update ON topics
  FOR UPDATE USING (public.is_workspace_member(workspace_id))
  WITH CHECK (public.is_workspace_member(workspace_id));
CREATE POLICY topics_delete ON topics
  FOR DELETE USING (public.is_workspace_member(workspace_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON topics TO authenticated;
