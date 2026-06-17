-- ============================================================
-- 0011_workspaces.sql
-- Phase 0 of the multi-user / BYOK plan (docs/SYSTEM1_BYOK_PLAN.md).
--
-- Introduces the WORKSPACE as the isolation unit:
--   * a workspace has one owner and any number of invited members (full collaborators)
--   * members of a workspace SHARE its data (topics, runs/reports, later stars/drafts)
--   * different workspaces are isolated from each other (enforced at the DB via RLS)
-- Also wires `runs` to an owning workspace and auto-provisions a personal workspace
-- for every new Supabase Auth user.
--
-- SAFETY — why enabling RLS here does NOT break the current System ①:
--   The existing web app + the GitHub-Actions pipeline connect with the Supabase
--   SECRET (service-role) key, which BYPASSES Row Level Security entirely. So every
--   current read/write keeps working unchanged. RLS only governs FUTURE per-user
--   access made with the anon key + a Supabase Auth JWT (auth.uid()).
--
-- Deferred to later phases (intentionally NOT in this migration):
--   * RLS on report_top20 / posts_archive — added when the per-user (anon-key) READ
--     path is built (Phase 3). Until then those are only ever read via the service key.
--   * Backfilling runs.workspace_id for the legacy rows #1..86 — done once Anna's
--     account + workspace exist (Phase 0-C), as a one-off UPDATE.
-- ============================================================

-- gen_random_uuid() needs pgcrypto (auto-loaded in Supabase, but be explicit — 0010 does
-- the same for pg_trgm).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ------------------------------------------------------------
-- 1) workspaces — the isolation unit (one owner)
-- ------------------------------------------------------------
CREATE TABLE workspaces (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  name        TEXT NOT NULL CHECK (length(trim(name)) > 0),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_workspaces_owner ON workspaces (owner_id);

-- ------------------------------------------------------------
-- 2) workspace_members — who can access a workspace
--    role: 'owner' (the creator) | 'member' (invited, full collaborator)
-- ------------------------------------------------------------
CREATE TABLE workspace_members (
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role         TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
  invited_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX idx_workspace_members_user ON workspace_members (user_id);

-- ------------------------------------------------------------
-- 3) runs gets an owning workspace
--    Nullable: legacy rows (#1..86) stay NULL until backfilled in Phase 0-C.
--    A NULL workspace_id row is invisible to every per-user (anon-key) query — only
--    the service key can see it — which is the desired behaviour pre-backfill.
--    ON DELETE SET NULL (not CASCADE): deleting a workspace must NOT wipe run history,
--    and posts_archive.run_id is RESTRICT (append-only archive) so CASCADE would also
--    conflict. Orphaned runs just fall back to service-key-only visibility.
-- ------------------------------------------------------------
ALTER TABLE runs ADD COLUMN workspace_id UUID REFERENCES workspaces(id) ON DELETE SET NULL;
CREATE INDEX idx_runs_workspace_id ON runs (workspace_id);

-- ------------------------------------------------------------
-- 4) membership helper — SECURITY DEFINER to avoid RLS recursion
--    Policies on workspace_members can't query workspace_members under RLS without
--    recursing; this function runs as owner (bypasses RLS) so it's safe to call from
--    policies. STABLE + locked search_path.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.is_workspace_member(ws UUID)
RETURNS BOOLEAN
LANGUAGE SQL
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM workspace_members m
    WHERE m.workspace_id = ws AND m.user_id = auth.uid()
  );
$$;

-- ------------------------------------------------------------
-- 5) auto-provision a personal workspace for every new auth user
--    Fires on signup: creates the workspace + the owner membership row.
--    SECURITY DEFINER so it can insert despite RLS.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  ws_id UUID;
BEGIN
  INSERT INTO workspaces (owner_id, name)
    VALUES (NEW.id, COALESCE(NULLIF(split_part(NEW.email, '@', 1), ''), 'me') || ' 的工作区')
    RETURNING id INTO ws_id;
  INSERT INTO workspace_members (workspace_id, user_id, role)
    VALUES (ws_id, NEW.id, 'owner');
  RETURN NEW;
EXCEPTION WHEN OTHERS THEN
  -- Never block a signup because workspace provisioning failed: log + continue.
  -- A user with no workspace can be provisioned manually / on next login.
  RAISE WARNING 'handle_new_user: workspace provisioning failed for user %: %', NEW.id, SQLERRM;
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- ------------------------------------------------------------
-- 6) RLS — turn it on + the per-user access policies
--    (service-role key still bypasses all of this.)
-- ------------------------------------------------------------
ALTER TABLE workspaces        ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs              ENABLE ROW LEVEL SECURITY;

-- workspaces: members can see their workspaces; only the owner can create/rename/delete.
CREATE POLICY workspaces_select ON workspaces
  FOR SELECT USING (public.is_workspace_member(id));
CREATE POLICY workspaces_insert ON workspaces
  FOR INSERT WITH CHECK (owner_id = auth.uid());
CREATE POLICY workspaces_update ON workspaces
  FOR UPDATE USING (owner_id = auth.uid()) WITH CHECK (owner_id = auth.uid());
CREATE POLICY workspaces_delete ON workspaces
  FOR DELETE USING (owner_id = auth.uid());

-- workspace_members: a member can see the member list of workspaces they belong to;
-- only the workspace OWNER can add/remove members.
CREATE POLICY workspace_members_select ON workspace_members
  FOR SELECT USING (public.is_workspace_member(workspace_id));
CREATE POLICY workspace_members_insert ON workspace_members
  FOR INSERT WITH CHECK (
    EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.owner_id = auth.uid())
  );
CREATE POLICY workspace_members_delete ON workspace_members
  FOR DELETE USING (
    user_id != auth.uid()  -- owner can't remove themselves and orphan the workspace
    AND EXISTS (SELECT 1 FROM workspaces w WHERE w.id = workspace_id AND w.owner_id = auth.uid())
  );

-- runs: a member can read runs belonging to their workspace(s).
-- (Writes stay on the service key — the pipeline runner — so no INSERT/UPDATE policy.)
CREATE POLICY runs_select ON runs
  FOR SELECT USING (workspace_id IS NOT NULL AND public.is_workspace_member(workspace_id));

-- ------------------------------------------------------------
-- 7) Grants — PostgREST checks table privileges in ADDITION to RLS.
--    Give the authenticated role exactly what the policies above allow.
-- ------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON workspaces        TO authenticated;
GRANT SELECT, INSERT, DELETE         ON workspace_members TO authenticated;
GRANT SELECT                         ON runs              TO authenticated;
