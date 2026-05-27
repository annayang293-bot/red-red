-- ============================================================
-- migration 0002 — report_top20 加 per-run 点评字段(Rex Step6 🔴1)
-- ============================================================
-- 背景:posts_archive 是 append-only「首次见到该帖的快照」(Rex 数据层 🔴1)。
-- 但 AI 点评(comment / 中文标题 xhs_title)是**每次 run 各异**的 per-run 结果,
-- 不能存回 posts_archive(否则同一帖第二次进报告,UI 读到的还是首次那条旧点评)。
-- → per-run 点评落到 report_top20(它本就已经按 run 存 tier)。
-- ------------------------------------------------------------
ALTER TABLE report_top20
  ADD COLUMN IF NOT EXISTS comment   TEXT,   -- 本次 run 对该帖的一句点评
  ADD COLUMN IF NOT EXISTS xhs_title TEXT;   -- 本次 run 的中文小红书标题(真 LLM 才有;heuristic 为空)

-- ============================================================
-- 完成. report_top20 现含 per-run 的 tier + comment + xhs_title。
-- ============================================================
