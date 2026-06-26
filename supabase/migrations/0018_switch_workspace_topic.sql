-- ============================================================
-- 0018_switch_workspace_topic.sql
-- Phase 3-6 (model B): workspace-scoped "switch/select active topic" RPC.
--
-- The legacy switch_active_topic(text) is single-tenant — it finds the active topic via
-- `WHERE status='active' LIMIT 1` (GLOBAL, no workspace) and INSERTs new topics with NO
-- workspace_id (→ a NULL-workspace topic, invisible to the per-workspace scoped reads). It is
-- unusable once topics are per-workspace. This is its workspace-scoped replacement.
--
-- Semantics (unchanged otherwise): within ONE workspace there is at most one `active` topic =
-- "the topic currently being viewed". POST /api/topics calls this to switch/add: archive that
-- workspace's current active, then re-activate the target (if it already exists in the workspace)
-- or insert it. Other topics stay `archived` but remain in the workspace's list and keep their own
-- auto_daily flag (auto_daily is independent of active/archived — the cron runs every auto_daily
-- topic regardless of which one is currently selected). The function body is the transaction; any
-- failure rolls back, so the workspace never ends up with 0 active.
--
-- Note: 0016's partial unique index uq_topics_workspace_keyword (keyword, workspace_id) guarantees
-- at most one topic per (keyword, workspace), so the target lookup needs no ORDER BY/LIMIT dance.
--
-- AuthZ: SECURITY INVOKER (NOT definer — it has no internal membership check; making it definer
-- would let any logged-in user operate on any workspace's topics by passing its id = an authZ hole).
-- EXECUTE is REVOKEd from anon/authenticated, so it's callable ONLY with the service-role key, from
-- the membership-gated /api/topics route (the /api/apify-token pattern).
-- ============================================================

CREATE OR REPLACE FUNCTION switch_workspace_topic(p_keyword TEXT, p_workspace_id UUID)
RETURNS topics
LANGUAGE plpgsql
AS $$
DECLARE
  v_active topics;
  v_target topics;
  v_result topics;
BEGIN
  IF p_keyword IS NULL OR btrim(p_keyword) = '' THEN
    RAISE EXCEPTION 'keyword cannot be empty';
  END IF;
  IF p_workspace_id IS NULL THEN
    RAISE EXCEPTION 'workspace_id cannot be null';
  END IF;
  p_keyword := btrim(p_keyword);

  -- Current active in THIS workspace (at most one).
  SELECT * INTO v_active FROM topics
    WHERE status = 'active' AND workspace_id = p_workspace_id
    LIMIT 1;

  -- Already the current topic → no-op.
  IF v_active.topic_id IS NOT NULL AND v_active.keyword = p_keyword THEN
    RETURN v_active;
  END IF;

  -- Archive this workspace's current active.
  IF v_active.topic_id IS NOT NULL THEN
    UPDATE topics SET status = 'archived', archived_at = NOW()
    WHERE topic_id = v_active.topic_id;
  END IF;

  -- Target already exists in this workspace (unique per (keyword, workspace)) → re-enable;
  -- otherwise insert a new topic owned by this workspace.
  SELECT * INTO v_target FROM topics
    WHERE keyword = p_keyword AND workspace_id = p_workspace_id
    LIMIT 1;

  IF v_target.topic_id IS NOT NULL THEN
    UPDATE topics SET status = 'active', archived_at = NULL
    WHERE topic_id = v_target.topic_id
    RETURNING * INTO v_result;
  ELSE
    INSERT INTO topics (keyword, status, workspace_id)
    VALUES (p_keyword, 'active', p_workspace_id)
    RETURNING * INTO v_result;
  END IF;

  RETURN v_result;  -- After switch, exactly 1 active in this workspace (failure rolls back).
END;
$$;

-- Service-key only (the route gates membership first); never exposed to anon/authenticated PostgREST.
REVOKE EXECUTE ON FUNCTION switch_workspace_topic(text, uuid) FROM anon, authenticated;
