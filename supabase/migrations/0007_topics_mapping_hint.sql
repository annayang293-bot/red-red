-- ============================================================
-- migration 0007 — topics: add mapping_hint TEXT (Anna 2026-05-28, option 3)
-- ============================================================
-- Business semantics: free-text guidance the user supplies for a topic, used to steer the
-- LLM at subreddit-mapping time. Example for keyword='Claude 教程':
--   mapping_hint = "重点 Claude API 给开发者看的教程; 不要游戏开发"
-- → resolve_topic injects this into the LLM prompt so picks lean toward dev tutorials
--   rather than gamedev (the failure mode where r/indiedev got picked instead of r/indiehackers).
--
-- - NULLABLE: most topics don't need a hint; only set it when the LLM picks the wrong direction.
-- - Editable: users can refine the hint later and trigger a cache refresh manually.
-- - Not exposed to the LLM keywords-relevance gate — that's a separate concern (filter, not mapping).
-- ------------------------------------------------------------
ALTER TABLE topics
  ADD COLUMN IF NOT EXISTS mapping_hint TEXT;

-- ============================================================
-- Done. resolve_topic reads this column and weaves it into the LLM prompt.
-- ============================================================
