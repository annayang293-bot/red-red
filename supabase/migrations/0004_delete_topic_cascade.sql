-- ============================================================
-- migration 0004 — 删主题级联(Anna 2026-05-26:删主题就把它的所有历史一起带走)
-- ============================================================
-- 业务语义:点 × 删主题 = 该主题彻底从系统消失。包括:
--   - 它名下所有 runs(运行记录)
--   - 这些 runs 的 report_top20(报告行)
--   - 这些 runs **首次**入库的 posts(若其它主题的 runs 还引用着这条 post 就不删,转移 run_id)
--   - 被删的 posts 上挂的 starred(收藏);其它 starred 的 run_id 指向被删 run 的 → 置 NULL(本来就 nullable)
-- 用单事务 plpgsql 函数,任一步失败整体回滚,不留破碎状态。
--
-- 注:posts_archive 是 append-only 共享主表(同一帖跨主题报告共用一行,首次 run_id=首次入库的 run)
-- 所以"删主题"对 posts 的处理是**条件性的**——只孤立、且只被这条主题引用的才真删。
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
  -- 找主题
  SELECT keyword, status INTO v_topic_keyword, v_topic_status
    FROM topics WHERE topic_id = p_topic_id;
  IF v_topic_keyword IS NULL THEN
    RAISE EXCEPTION 'topic_not_found';
  END IF;
  IF v_topic_status = 'active' THEN
    RAISE EXCEPTION 'cannot_delete_active_topic';
  END IF;

  -- 收集这个 topic 名下的 runs
  SELECT COALESCE(ARRAY_AGG(run_id), ARRAY[]::BIGINT[])
    INTO v_run_ids FROM runs WHERE topic_id = p_topic_id;

  IF cardinality(v_run_ids) > 0 THEN
    -- 1) 删 report_top20(这些 runs 的报告行)
    WITH d AS (DELETE FROM report_top20 WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_reports_deleted FROM d;

    -- 2) 哪些 post 还能"被救"?其它 run(不在 v_run_ids 里)的 report_top20 还引用着 → 转移 run_id
    --    (注:run_id 是 NOT NULL FK,不能 NULL,必须指向一个真存在的 run)
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

    -- 3) 剩下的 post(只被本主题引用)→ 先删它们的 starred,再删 post
    WITH ds AS (
      DELETE FROM starred WHERE post_id IN (
        SELECT post_id FROM posts_archive WHERE run_id = ANY(v_run_ids)
      ) RETURNING 1
    )
    SELECT COUNT(*) INTO v_starred_deleted FROM ds;
    WITH dp AS (DELETE FROM posts_archive WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_posts_deleted FROM dp;

    -- 4) starred.run_id 是可空 FK:指向被删 run 的 → 置 NULL(保留收藏记录本身)
    UPDATE starred SET run_id = NULL WHERE run_id = ANY(v_run_ids);

    -- 5) 删 runs
    WITH dr AS (DELETE FROM runs WHERE run_id = ANY(v_run_ids) RETURNING 1)
    SELECT COUNT(*) INTO v_runs_deleted FROM dr;
  END IF;

  -- 6) 最后删 topic 本身
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
-- 完成. 前端 DELETE /api/topics 改调 rpc('delete_topic_cascade').
-- ============================================================
