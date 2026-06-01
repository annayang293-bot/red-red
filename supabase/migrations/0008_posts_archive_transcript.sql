-- ============================================================
-- migration 0008 — posts_archive: add 3 transcript columns (Anna 2026-06-01)
-- ============================================================
-- Business semantics: enables System ② to draft from the substance of Reddit video posts
-- (e.g. the Hinton LBC interview that motivated this), not just title + comments. The
-- pipeline transcribes the v.redd.it audio via OpenAI Whisper for any Top-N post whose
-- harshmaur metadata is `postType: hosted:video`. Failure is per-post and silent (post still
-- ships with these columns NULL); not bubbling to `runs.status`.
--
-- Column design:
-- - `transcript`            : full transcribed text. Capped only by Whisper's input limit
--   (~25 MB audio → arbitrary text length). Nullable: most posts aren't videos.
-- - `transcript_lang`       : ISO-ish language tag Whisper auto-detects ("english", "chinese",
--   etc.). Useful to filter / segment for downstream analytics. Nullable for the same reason
--   as transcript.
-- - `transcript_cost_usd`   : per-video Whisper cost in USD (duration_seconds × $0.006/min).
--   Tracked so we can roll-up monthly Apify-equivalent spend against Anna's budget cap.
--   NUMERIC(8,4) — at $0.006/min, even a 99-hour video stays inside 8 total / 4 fractional.
--
-- Indexing: none. We don't query "all videos with transcripts" or "transcripts in language X"
-- on the hot path. If that changes (operator dashboard, etc.), add then.
-- ------------------------------------------------------------
ALTER TABLE posts_archive
  ADD COLUMN IF NOT EXISTS transcript            TEXT,
  ADD COLUMN IF NOT EXISTS transcript_lang       TEXT,
  ADD COLUMN IF NOT EXISTS transcript_cost_usd   NUMERIC(8,4);

-- ============================================================
-- Done. Pipeline writes these in pipeline/store.py::_post_row when runner._enrich_top_with_transcripts
-- produces a non-None result for the post.
-- ============================================================
