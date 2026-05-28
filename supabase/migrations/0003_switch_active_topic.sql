-- ============================================================
-- migration 0003 — wrap topic hard-switch into a single-transaction RPC (Rex Step 7 🔴2)
-- ============================================================
-- Background: the frontend / API originally did the hard switch via 3 independent PostgREST calls
-- (find active → archive old → enable new). Non-transactional → if "archive old active succeeded but
-- enable new active failed", the system would be stuck with **0 active topics**.
-- The business semantics is "hard switch" (after switching, exactly 1 active topic), not "clear then try".
-- → Consolidated into a single plpgsql function (the function body is the transaction; any failure rolls back),
-- and the frontend calls only this entry point.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION switch_active_topic(p_keyword TEXT)
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
  p_keyword := btrim(p_keyword);

  -- Current active (at most one)
  SELECT * INTO v_active FROM topics WHERE status = 'active' LIMIT 1;

  -- Already the current topic → no-op
  IF v_active.topic_id IS NOT NULL AND v_active.keyword = p_keyword THEN
    RETURN v_active;
  END IF;

  -- Archive the current active
  IF v_active.topic_id IS NOT NULL THEN
    UPDATE topics SET status = 'archived', archived_at = NOW()
    WHERE topic_id = v_active.topic_id;
  END IF;

  -- Target keyword already exists (take the most recent one) → re-enable; otherwise insert new
  SELECT * INTO v_target FROM topics
    WHERE keyword = p_keyword ORDER BY started_at DESC LIMIT 1;

  IF v_target.topic_id IS NOT NULL THEN
    UPDATE topics SET status = 'active', archived_at = NULL
    WHERE topic_id = v_target.topic_id
    RETURNING * INTO v_result;
  ELSE
    INSERT INTO topics (keyword, status) VALUES (p_keyword, 'active')
    RETURNING * INTO v_result;
  END IF;

  RETURN v_result;  -- After switch, exactly 1 active (failure rolls everything back, never leaves 0 active)
END;
$$;

-- ============================================================
-- Done. The frontend's POST /api/topics now calls rpc('switch_active_topic').
-- ============================================================
