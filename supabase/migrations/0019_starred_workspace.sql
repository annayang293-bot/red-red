-- ============================================================
-- 0019_starred_workspace.sql
-- Phase 3: make the starred library SHARED PER WORKSPACE (Anna 2026-06-25).
--
-- Decided behavior: within one workspace there is ONE shared star library — if a member stars a
-- post, every member sees it as starred and can star others; different workspaces are isolated.
-- The legacy model keyed stars by a free-text `person` ('anna'/'junxi'/…), which (a) doesn't isolate
-- workspaces and (b) would collide for a second user (the API defaults person='anna'). Re-key by
-- workspace_id.
--
-- Changes:
--   1. add starred.workspace_id (ON DELETE SET NULL, consistent with runs/topics — deleting a
--      workspace orphans stars to service-key-only visibility rather than cascading them away).
--   2. backfill existing rows (all currently person='anna') → Anna's workspace.
--   3. person becomes nullable — System ① no longer writes it (workspace_id is the key). Column kept
--      (not dropped) so historical rows + the stashed System ② draft pipeline that still references
--      it aren't broken at the schema level.
--   4. swap the active-star uniqueness from (person, post_id) → (workspace_id, post_id): one active
--      star per post per workspace (soft-delete history preserved; re-star after unstar allowed).
--   5. RLS: a workspace member can read/manage that workspace's stars (parity with topics/runs;
--      the API still uses the service key + resolveCaller gate, so this is defense-in-depth).
--
-- NOTE (deferred reconciliation): the stashed System ② star.ts inserts with `person` and no
-- workspace_id, and dedups on the old (person, post_id) index. When System ② is un-stashed it must
-- be updated to set workspace_id and dedup on the new index. Accepted cost of deprioritizing ②.
-- ============================================================

ALTER TABLE starred ADD COLUMN workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;
CREATE INDEX idx_starred_workspace ON starred (workspace_id);

-- Backfill: every existing star is Anna's (person='anna').
UPDATE starred SET workspace_id = 'b6f4fe6a-d989-4e52-abb0-f1c3f5d28b5f' WHERE workspace_id IS NULL;

-- System ① stops writing person; keep the column but allow NULL.
ALTER TABLE starred ALTER COLUMN person DROP NOT NULL;

-- Swap the active-star uniqueness to per-workspace.
DROP INDEX IF EXISTS uq_starred_active;
CREATE UNIQUE INDEX uq_starred_active_ws ON starred (workspace_id, post_id)
  WHERE deleted_at IS NULL AND workspace_id IS NOT NULL;

-- RLS: workspace members can read/manage their workspace's stars. Service-role key bypasses all of
-- this (the API path); this governs any future direct anon/authenticated PostgREST access.
ALTER TABLE starred ENABLE ROW LEVEL SECURITY;

CREATE POLICY starred_select ON starred
  FOR SELECT USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY starred_insert ON starred
  FOR INSERT WITH CHECK (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));
CREATE POLICY starred_update ON starred
  FOR UPDATE USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id))
  WITH CHECK (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));

GRANT SELECT, INSERT, UPDATE ON starred TO authenticated;
