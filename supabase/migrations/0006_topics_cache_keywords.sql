-- ============================================================
-- migration 0006 — topics_cache: add keywords JSONB (Anna 2026-05-27: same topic chose different subreddits each run)
-- ============================================================
-- Business semantics: resolve_topic used to call the LLM on every run; gpt-4o-mini isn't fully
-- deterministic (temperature 0.3), so the same topic "AI startup" could yield different
-- subreddits/keywords across runs → inconsistent UX.
-- Fix: use topics_cache for "cache by topic_keyword" (originally 7-day TTL per schema design, now
-- read as permanent per Anna 2026-05-28); on hit, reuse directly without asking the LLM.
-- topics_cache already had subreddits JSONB; **adding keywords JSONB** so per-topic relevance
-- keywords are cached together.
-- ------------------------------------------------------------
ALTER TABLE topics_cache
  ADD COLUMN IF NOT EXISTS keywords JSONB;

-- ============================================================
-- Done. resolve_topic reads topics_cache; on hit use it; otherwise call the LLM + write back to cache.
-- ============================================================
