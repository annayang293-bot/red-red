-- ============================================================
-- migration 0002 — report_top20: add per-run review fields (Rex Step 6 🔴1)
-- ============================================================
-- Background: posts_archive is the append-only "first-seen snapshot" of the post (Rex data-layer 🔴1).
-- But the AI review (comment / Chinese title xhs_title) is a **per-run** result that differs every run —
-- it cannot go back into posts_archive (otherwise the next time the same post re-enters the report,
-- the UI would read the first run's stale review).
-- → Per-run review lands in report_top20 (which already stores tier per run).
-- ------------------------------------------------------------
ALTER TABLE report_top20
  ADD COLUMN IF NOT EXISTS comment   TEXT,   -- this run's one-liner critique for the post
  ADD COLUMN IF NOT EXISTS xhs_title TEXT;   -- this run's Chinese Xiaohongshu title (only when real LLM is used; empty for heuristic)

-- ============================================================
-- Done. report_top20 now carries per-run tier + comment + xhs_title.
-- ============================================================
