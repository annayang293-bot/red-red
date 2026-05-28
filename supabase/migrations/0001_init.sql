-- ============================================================
-- System ① — initial schema (migration 0001)
-- Derived from System ①_PRD_v1.md + Anna 2026-05-21 ratified 12 items
-- ============================================================
-- Design principles:
--   • 5 must-reserves (post Cindy + Richard research):
--     1. posts_archive: (source, source_native_id) UNIQUE + post_id PK;
--        starred references post_id via FK, does not duplicate data
--     2. Every post must carry config_fingerprint (used by System ③ V2 calibration)
--     3. Every table carries created_at + updated_at (UTC = TIMESTAMPTZ)
--     4. "Cancellable" tables (starred) use deleted_at NULLABLE soft delete
--     5. full_content does NOT live in DB — stored in Supabase Storage; DB only keeps a url ref
--   • Forward-compat: sources is a registry; future Xiaohongshu / X / LinkedIn adapters won't change structure.
--   • Topic-driven routine model: topics carries status (active/archived), hard switch.
-- ============================================================


-- ------------------------------------------------------------
-- 1) sources — data-source registry (pluggable abstraction)
--    One row per data source (Reddit / PH / future XHS), recording adapter class + Top-20 quota etc.
-- ------------------------------------------------------------
CREATE TABLE sources (
  source_id      BIGSERIAL PRIMARY KEY,
  source_key     TEXT NOT NULL UNIQUE,          -- 'reddit' / 'product_hunt' / 'xiaohongshu'
  display_name   TEXT NOT NULL,
  adapter_class  TEXT NOT NULL,                 -- Python class name, e.g. 'RedditSource'
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  quota_top20    INT NOT NULL DEFAULT 0,        -- Top-20 quota (PH=2, Reddit unlimited=0)
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Initial seed: Reddit + PH (XHS to be added later)
INSERT INTO sources (source_key, display_name, adapter_class, enabled, quota_top20, notes) VALUES
  ('reddit',       'Reddit',        'RedditSource',       TRUE, 0, 'Primary source, uses the public hot.json endpoint'),
  ('product_hunt', 'Product Hunt',  'ProductHuntSource',  TRUE, 2, 'RSS, no vote data, Top-20 quota of 2 reserves a narrow trend window');


-- ------------------------------------------------------------
-- 2) topics — topics (active/archived, hard switch)
--    Anna sets the active topic; the system runs its routine against it;
--    switching = old active → archived, new topic → active (at most 1 active at any time).
-- ------------------------------------------------------------
CREATE TABLE topics (
  topic_id      BIGSERIAL PRIMARY KEY,
  keyword       TEXT NOT NULL,                  -- user-entered topic keyword, e.g. 'AI 创业'
  status        TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  created_by    TEXT,                           -- anna / junxi / carrie
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archived_at   TIMESTAMPTZ,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- status binds to archived_at: active must not be archived, archived must carry an archive time
  CHECK (
    (status = 'active'   AND archived_at IS NULL) OR
    (status = 'archived' AND archived_at IS NOT NULL)
  )
);

-- Constraint: at most 1 active topic at any time (partial unique index)
CREATE UNIQUE INDEX uq_topics_one_active ON topics (status) WHERE status = 'active';

CREATE INDEX idx_topics_status ON topics (status);
CREATE INDEX idx_topics_started_at ON topics (started_at DESC);


-- ------------------------------------------------------------
-- 3) topics_cache — topic → subreddit mapping cache
--    PRD §4 step 4: 7-day TTL + cached_at + stale flag + 30-day hard ceiling
-- ------------------------------------------------------------
CREATE TABLE topics_cache (
  cache_id          BIGSERIAL PRIMARY KEY,
  topic_keyword     TEXT NOT NULL UNIQUE,       -- cache key
  subreddits        JSONB NOT NULL,             -- [{name, relevance_score, quality_score, source: search/llm/synonym}]
  allow_list_applied JSONB,                     -- which allow-list entries were applied
  deny_list_applied  JSONB,                     -- which deny-list entries were applied
  cached_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at        TIMESTAMPTZ NOT NULL,       -- cached_at + 7 days (TTL)
  hard_ceiling_at   TIMESTAMPTZ NOT NULL,       -- cached_at + 30 days (past this = cache miss)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topics_cache_expires ON topics_cache (expires_at);


-- ------------------------------------------------------------
-- 4) operator_lists — allow/deny list (operator safety-net layer)
--    PRD §4 step 3: whitelist forces inclusion / blacklist permanently drops
--    scope_topic_id = NULL → applies globally to all topics
--    scope_topic_id pointing at a topic → only applies to that topic
-- ------------------------------------------------------------
CREATE TABLE operator_lists (
  list_id        BIGSERIAL PRIMARY KEY,
  list_type      TEXT NOT NULL CHECK (list_type IN ('allow', 'deny')),
  subreddit_name TEXT NOT NULL,                 -- 'OpenAI' / 'funny' / etc.
  scope_topic_id BIGINT REFERENCES topics(topic_id),  -- NULL = global
  notes          TEXT,
  created_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_operator_lists_scope ON operator_lists (scope_topic_id, list_type);


-- ------------------------------------------------------------
-- 5) runs — metadata for each "run" (cron-triggered or user-triggered)
--    Q3 ratification: independent run per day + archive accumulation; run is the unit of analysis.
-- ------------------------------------------------------------
CREATE TABLE runs (
  run_id              BIGSERIAL PRIMARY KEY,
  topic_id            BIGINT NOT NULL REFERENCES topics(topic_id),
  topic_keyword       TEXT NOT NULL,            -- denormalized for ease of querying
  triggered_by        TEXT NOT NULL CHECK (triggered_by IN ('cron', 'manual')),
  triggered_by_person TEXT,                     -- anna / junxi / carrie / 'system' (cron)
  status              TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at         TIMESTAMPTZ,
  posts_count         INT,                      -- full set count after the three-gate filter (30-60)
  top20_count         INT,                      -- actual rows in the MD daily report (may be <20 after cross-day dedup)
  ai_mode             TEXT CHECK (ai_mode IN ('ai', 'heuristic')),  -- AI review mode
  sanity_status       TEXT CHECK (sanity_status IN ('OK', 'OK_WITH_ANOMALY', 'FAIL')),
  sanity_anomalies    JSONB,                    -- list of anomalies (if any)
  config_fingerprint  TEXT NOT NULL,            -- config fingerprint for this run (used by System ③ V2 calibration grouping)
  error_message       TEXT,                     -- error message when failed
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_runs_topic_id ON runs (topic_id);
CREATE INDEX idx_runs_started_at ON runs (started_at DESC);
CREATE INDEX idx_runs_status ON runs (status);
CREATE INDEX idx_runs_fingerprint ON runs (config_fingerprint);


-- ------------------------------------------------------------
-- 6) posts_archive — full accumulated set of posts
--    Cindy must-reserve #1: (source, source_native_id) UNIQUE prevents duplicates + post_id PK;
--    starred / report_top20 reference post_id via FK.
--    Richard required: full_content does NOT live here, it goes to Supabase Storage (full_content_url).
-- ------------------------------------------------------------
CREATE TABLE posts_archive (
  post_id            BIGSERIAL PRIMARY KEY,
  -- Source identity (prevents the same post being stored twice)
  source             TEXT NOT NULL REFERENCES sources(source_key),
  source_native_id   TEXT NOT NULL,             -- source-native ID, e.g. reddit '1tgu8va'
  -- Content
  title              TEXT NOT NULL,
  url                TEXT NOT NULL,
  raw_snippet        TEXT,                      -- ≤500 chars excerpt (lightweight, queryable in DB)
  full_content_url   TEXT,                      -- points to the .json.gz full-text file in Supabase Storage
  -- Engagement metrics
  raw_metrics        JSONB,                     -- {likes, comments, crossposts}
  -- Scoring
  hot_score          REAL,                      -- 0-100, normalized
  relevance_score    REAL,                      -- 0-1
  -- Three-layer tags (PRD §9)
  tags_json          JSONB,                     -- {domain: [...], entity: [...], intent: '...'}
  -- AI review (PRD §10.2.2)
  ai_review          JSONB,                     -- {xhs_title, comment, tier (强/中/弱), mode (ai/heuristic)}
  comments_summary   JSONB,                     -- summary of top 8-10 comments (used for meme double-check + drafting reference)
  -- Timing
  published_at       TIMESTAMPTZ,
  fetched_at         TIMESTAMPTZ,
  -- Relations
  run_id             BIGINT NOT NULL REFERENCES runs(run_id),
  config_fingerprint TEXT NOT NULL,             -- required (used by System ③ V2 calibration)
  -- Meta
  source_native      JSONB,                     -- {subreddit, flair, permalink, ...} — native metadata
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- Constraint
  UNIQUE (source, source_native_id)             -- prevents the same post being stored multiple times across runs
);

CREATE INDEX idx_posts_archive_run_id ON posts_archive (run_id);
CREATE INDEX idx_posts_archive_source ON posts_archive (source);
CREATE INDEX idx_posts_archive_hot_score ON posts_archive (hot_score DESC);
CREATE INDEX idx_posts_archive_published_at ON posts_archive (published_at DESC);
CREATE INDEX idx_posts_archive_fetched_at ON posts_archive (fetched_at DESC);
CREATE INDEX idx_posts_archive_fingerprint ON posts_archive (config_fingerprint);
-- GIN index on tags_json reserved for future search by entity/intent (System ②/③ use)
CREATE INDEX idx_posts_archive_tags_gin ON posts_archive USING GIN (tags_json);


-- ------------------------------------------------------------
-- 7) report_top20 — Top-20 report per run (the rendering source for "today's report" in the frontend)
--    PRD §10.2 entry point for chief-editor review + star.
-- ------------------------------------------------------------
CREATE TABLE report_top20 (
  report_id   BIGSERIAL PRIMARY KEY,
  run_id      BIGINT NOT NULL REFERENCES runs(run_id),
  post_id     BIGINT NOT NULL REFERENCES posts_archive(post_id),
  rank        INT NOT NULL CHECK (rank BETWEEN 1 AND 20),  -- Top 20 (may be <20 after cross-day dedup)
  tier        TEXT CHECK (tier IN ('强', '中', '弱')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, rank),
  UNIQUE (run_id, post_id)   -- a post can appear in the same run's report at most once (guards against upstream dedup bugs producing duplicate rows)
);

CREATE INDEX idx_report_top20_run_id ON report_top20 (run_id);


-- ------------------------------------------------------------
-- 8) starred — chief-editor starred library (per person, soft delete)
--    Cindy must-reserve #1 + #4: post_id FK (no data duplication) + deleted_at NULLABLE.
--    System ② drafting prioritizes consuming from here; System ③ analyzes chief-editor preference.
-- ------------------------------------------------------------
CREATE TABLE starred (
  star_id     BIGSERIAL PRIMARY KEY,
  person      TEXT NOT NULL,                    -- 'anna' / 'junxi' / 'carrie'
  post_id     BIGINT NOT NULL REFERENCES posts_archive(post_id),
  run_id      BIGINT REFERENCES runs(run_id),   -- which run the star happened in (nullable for batch back-fill)
  starred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at  TIMESTAMPTZ,                      -- soft delete (filled in on unstar)
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Constraint: a given person can only have 1 active star on a given post at a time
-- (soft-delete history is preserved, so re-starring after an unstar is allowed).
CREATE UNIQUE INDEX uq_starred_active ON starred (person, post_id) WHERE deleted_at IS NULL;

CREATE INDEX idx_starred_person ON starred (person);
CREATE INDEX idx_starred_post_id ON starred (post_id);
CREATE INDEX idx_starred_starred_at ON starred (starred_at DESC);


-- ------------------------------------------------------------
-- 9) suggested_keywords — keyword-list growth tracking (PRD §5.1.2)
--    High-frequency entities extracted by AI that aren't in keywords.yaml are auto-logged for monthly operator review.
-- ------------------------------------------------------------
CREATE TABLE suggested_keywords (
  id                 BIGSERIAL PRIMARY KEY,
  tag_layer          TEXT NOT NULL CHECK (tag_layer IN ('domain', 'entity')),
  tag_value          TEXT NOT NULL,
  occurrence_count   INT NOT NULL DEFAULT 1,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reviewed           BOOLEAN NOT NULL DEFAULT FALSE,    -- has the operator reviewed it?
  reviewed_decision  TEXT CHECK (reviewed_decision IN ('add', 'reject')),  -- NULL auto-allows (unreviewed); the list does NOT include NULL, otherwise illegal values would slip through the constraint
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tag_layer, tag_value)
);

CREATE INDEX idx_suggested_keywords_reviewed ON suggested_keywords (reviewed);
CREATE INDEX idx_suggested_keywords_last_seen ON suggested_keywords (last_seen_at DESC);


-- ============================================================
-- Trigger: auto-maintain updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach the trigger to every table that has updated_at
DO $$
DECLARE
  tbl TEXT;
BEGIN
  FOR tbl IN
    SELECT unnest(ARRAY['sources','topics','topics_cache','operator_lists','runs',
                        'posts_archive','starred','suggested_keywords'])
  LOOP
    EXECUTE format('CREATE TRIGGER set_updated_at BEFORE UPDATE ON %I '
                   'FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at()', tbl);
  END LOOP;
END $$;


-- ============================================================
-- Done. 9 tables + indexes + constraints + triggers.
-- ============================================================
