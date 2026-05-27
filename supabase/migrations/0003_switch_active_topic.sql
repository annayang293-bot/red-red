-- ============================================================
-- migration 0003 — 主题硬切换做成单事务 RPC(Rex Step7 🔴2)
-- ============================================================
-- 背景:前端/API 原来用 3 次独立 PostgREST 调用做硬切换(查 active → 归档旧 → 启用新)。
-- 非事务 → 若"归档旧 active 成功、启用新 active 失败",系统会卡在 **0 个 active topic**。
-- 业务语义是"硬切换"(切完必有且仅有 1 个 active),不是"先清空再试"。
-- → 收进**单个 plpgsql 函数**(函数体即事务,任一步失败整体回滚),前端只调这一个入口。
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
    RAISE EXCEPTION 'keyword 不能为空';
  END IF;
  p_keyword := btrim(p_keyword);

  -- 当前 active(最多一个)
  SELECT * INTO v_active FROM topics WHERE status = 'active' LIMIT 1;

  -- 已经是当前主题 → no-op
  IF v_active.topic_id IS NOT NULL AND v_active.keyword = p_keyword THEN
    RETURN v_active;
  END IF;

  -- 归档当前 active
  IF v_active.topic_id IS NOT NULL THEN
    UPDATE topics SET status = 'archived', archived_at = NOW()
    WHERE topic_id = v_active.topic_id;
  END IF;

  -- 目标 keyword 已存在(取最近一条)→ 重新启用;否则新建
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

  RETURN v_result;  -- 切完恒有且仅有 1 个 active(失败则整体回滚,不会留 0 active)
END;
$$;

-- ============================================================
-- 完成. 前端 POST /api/topics 改调 rpc('switch_active_topic')。
-- ============================================================
