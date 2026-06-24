-- ============================================================
-- 0014_topics_workspace.sql
-- Phase 3-A (model B): make `topics` per-workspace + add the `auto_daily` opt-in toggle.
--
-- Additive + non-breaking: adds workspace_id + auto_daily, backfills legacy rows to Anna's
-- workspace, enables RLS. Keeps `status` / `switch_active_topic()` and the daily cron working;
-- later Phase 3 steps move report reads + cron to the per-(workspace, topic) model and retire
-- single-active. The service-role key (run_once / existing /api/topics) bypasses RLS, so enabling
-- RLS here does not break current System ①.
--
-- Review fixes folded in (Phase 3-1):
--   * ON DELETE SET NULL (not CASCADE) — matches runs (0011); avoids a RESTRICT collision with
--     runs.topic_id and never wipes topic/run history on workspace delete.
--   * backfill is IN this migration (atomic) so legacy topics aren't invisible to authenticated users.
--   * update/delete policies carry the same `workspace_id IS NOT NULL` guard as select.
--   * switch_active_topic / delete_topic_cascade get SECURITY DEFINER so they keep working once a
--     Phase-3 UI calls them via the user JWT (else RLS would filter their internal reads to nothing).
-- ============================================================

ALTER TABLE topics ADD COLUMN workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;
ALTER TABLE topics ADD COLUMN auto_daily BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX idx_topics_workspace ON topics (workspace_id);

-- Backfill: existing (legacy) topics belong to Anna's workspace; her active topic auto-runs daily.
-- (Hardcoded workspace id is acceptable for this one-off project migration.)
UPDATE topics SET workspace_id = 'b6f4fe6a-d989-4e52-abb0-f1c3f5d28b5f' WHERE workspace_id IS NULL;
UPDATE topics SET auto_daily = true
  WHERE workspace_id = 'b6f4fe6a-d989-4e52-abb0-f1c3f5d28b5f' AND status = 'active';

-- RLS: a workspace member can read/manage that workspace's topics.
ALTER TABLE topics ENABLE ROW LEVEL SECURITY;

CREATE POLICY topics_select ON topics
  FOR SELECT USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY topics_insert ON topics
  FOR INSERT WITH CHECK (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY topics_update ON topics
  FOR UPDATE USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id))
  WITH CHECK (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY topics_delete ON topics
  FOR DELETE USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON topics TO authenticated;

-- Lock the topic RPCs to the service key. They stay SECURITY INVOKER and are called ONLY from
-- membership-gated server routes (the /api/apify-token pattern) with the service-role key — which
-- bypasses RLS, so their internal topic reads keep working. We REVOKE EXECUTE from anon/authenticated
-- so a logged-in user can't call them via PostgREST RPC: these functions have no internal auth.uid()
-- membership check, so exposing them (especially as SECURITY DEFINER, which is why we did NOT use it)
-- would let anyone operate on any topic by id — an authZ hole. Service routes gate membership first.
REVOKE EXECUTE ON FUNCTION switch_active_topic(text) FROM anon, authenticated;
REVOKE EXECUTE ON FUNCTION delete_topic_cascade(bigint) FROM anon, authenticated;
