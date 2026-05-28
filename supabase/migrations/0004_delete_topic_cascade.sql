-- ============================================================
-- migration 0004 — cascade topic delete (Anna 2026-05-26: deleting a topic should take all its history along)
-- ============================================================
-- Business semantics: clicking × to delete a topic = it disappears completely from the system. Including:
--   - all runs under that topic (run records)
--   - those runs' report_top20 rows (report rows)
--   - posts those runs **first-inserted** into the archive (if other topics' runs still reference the post,
--     don't delete — reassign the run_id instead)
--   - starred entries attached to the deleted posts; other starred whose run_id points at deleted runs → set NULL
--     (the column is already nullable)
-- Wrapped as a single plpgsql function; any step's failure rolls everything back, never leaving a broken state.
--
-- Note: posts_archive is an append-only shared main table (the same post is shared across reports from different
-- topics; first-seen run_id = the run that first inserted the post). So "delete topic" treats posts conditionally —
-- only orphan posts (referenced only by this topic) are truly deleted.
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION delete_topic_cascade(p_topic_id BIGINT)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
  v_run_ids        BIGINT[];
  v_posts_reassigned INT := 0;
  v_posts_deleted    INT := 0;
  v_starred_deleted  INT := 0;
  v_reports_deleted  INT := 0;
  v_runs_deleted     INT := 0;
  v_topic_keyword    TEXT;
  v_topic_status     TEXT;
BEGIN
  -- Find the topic
  SELECT keyword, status INTO v_topic_keyword, v_topic_status
    FROM topics WHERE topic_id = p_topic_id;
  IF v_topic_keyword IS NULL THEN
    RAISE EXCEPTION 'topic_not_found';
  END IF;
  IF v_topic_status = 'active' THEN
    RAISE EXCEPTION 'cannot_delete_active_topic';
  END IF;

  -- Collect runs belonging to this topic
  SELECT COALESCE(ARRAY_AGG(run_id), ARRAY[]::BIGINT[])
    INTO v_run_ids FROM runs WHERE topic_id = p_topic_id;

  IF cardinality(v_run_ids) > 0 THEN
    -- 1) Delete report_top20 (these runs' report rows)
    WITH d AS (DELETE FROM report_top20 WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_reports_deleted FROM d;

    -- 2) Which posts can be "rescued"? Posts referenced by another run's report_top20 (run not in v_run_ids)
    --    → reassign run_id to the surviving reference.
    --    (Note: run_id is a NOT NULL FK; it can't be NULL — it must point to an existing run.)
    WITH save AS (
      UPDATE posts_archive p
      SET run_id = (
        SELECT MIN(rt.run_id) FROM report_top20 rt
        WHERE rt.post_id = p.post_id AND NOT (rt.run_id = ANY(v_run_ids))
      )
      WHERE p.run_id = ANY(v_run_ids)
        AND EXISTS (
          SELECT 1 FROM report_top20 rt2
          WHERE rt2.post_id = p.post_id AND NOT (rt2.run_id = ANY(v_run_ids))
        )
      RETURNING 1
    )
    SELECT COUNT(*) INTO v_posts_reassigned FROM save;

    -- 3) Remaining posts (only referenced by this topic) → delete their starred entries first, then the posts
    WITH ds AS (
      DELETE FROM starred WHERE post_id IN (
        SELECT post_id FROM posts_archive WHERE run_id = ANY(v_run_ids)
      ) RETURNING 1
    )
    SELECT COUNT(*) INTO v_starred_deleted FROM ds;
    WITH dp AS (DELETE FROM posts_archive WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_posts_deleted FROM dp;

    -- 4) starred.run_id is a nullable FK: those pointing at deleted runs → set NULL (preserve the star record itself)
    UPDATE starred SET run_id = NULL WHERE run_id = ANY(v_run_ids);

    -- 5) Delete the runs
    WITH dr AS (DELETE FROM runs WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_runs_deleted FROM dr;
  END IF;

  -- 6) Finally delete the topic itself
  DELETE FROM topics WHERE topic_id = p_topic_id;

  RETURN jsonb_build_object(
    'ok', true,
    'topic_keyword', v_topic_keyword,
    'runs_deleted', v_runs_deleted,
    'reports_deleted', v_reports_deleted,
    'posts_reassigned', v_posts_reassigned,
    'posts_deleted', v_posts_deleted,
    'starred_deleted', v_starred_deleted
  );
END;
$$;

-- ============================================================
-- Done. The frontend's DELETE /api/topics now calls rpc('delete_topic_cascade').
-- ============================================================
