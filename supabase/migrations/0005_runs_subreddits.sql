-- ============================================================
-- migration 0005 — runs: add subreddits JSONB (Anna 2026-05-27: make "topic mapping" more visible in the UI)
-- ============================================================
-- Business semantics: each run saves the **full subreddit list it actually fetched** (all of them,
-- including those that fetched but didn't produce a Top item). The frontend TopicPanel uses this to
-- show "AI picked these subreddits", making the topic mapping observable — not just "subreddits that
-- appear in this report" (which is only the subset that produced Top items).
-- ------------------------------------------------------------
ALTER TABLE runs
  ADD COLUMN IF NOT EXISTS subreddits JSONB;

-- ============================================================
-- Done. The pipeline writes ARRAY['sub1','sub2',...] during save().
-- ============================================================
