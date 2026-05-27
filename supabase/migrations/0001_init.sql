-- ============================================================
-- 系统① — 初始 schema (migration 0001)
-- 基于 系统①_PRD_v1.md + Anna 2026-05-21 拍定 12 条 ratification
-- ============================================================
-- 设计原则:
--   • 5 项 must-reserve (Cindy + Richard 调研后):
--     1. posts_archive 用 (source, source_native_id) UNIQUE + post_id PK;
--        starred 用 FK 引 post_id,不 dup 数据
--     2. 所有 posts 必带 config_fingerprint(系统③ V2 校准用)
--     3. 所有表必带 created_at + updated_at(UTC = TIMESTAMPTZ)
--     4. 可"取消"的表(starred)用 deleted_at NULLABLE 软删除
--     5. full_content 不进 DB — 存 Supabase Storage,DB 只存 url ref
--   • Forward-compat: sources 表抽象,小红书/X/LinkedIn 未来加 adapter 不动结构
--   • 主题驱动 routine 模式: topics 表带 status (active/archived),hard switch
-- ============================================================


-- ------------------------------------------------------------
-- 1) sources — 数据源注册表(可插拔抽象层)
--    每个数据源(Reddit/PH/未来 XHS)一行,记录 adapter 类名 + Top 20 配额等
-- ------------------------------------------------------------
CREATE TABLE sources (
  source_id      BIGSERIAL PRIMARY KEY,
  source_key     TEXT NOT NULL UNIQUE,          -- 'reddit' / 'product_hunt' / 'xiaohongshu'
  display_name   TEXT NOT NULL,
  adapter_class  TEXT NOT NULL,                 -- Python 类名,如 'RedditSource'
  enabled        BOOLEAN NOT NULL DEFAULT TRUE,
  quota_top20    INT NOT NULL DEFAULT 0,        -- Top 20 名额配额(PH=2, Reddit 不限=0)
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 初始 seed:Reddit + PH(XHS 未来加)
INSERT INTO sources (source_key, display_name, adapter_class, enabled, quota_top20, notes) VALUES
  ('reddit',       'Reddit',        'RedditSource',       TRUE, 0, '主源,走 hot.json 公开接口'),
  ('product_hunt', 'Product Hunt',  'ProductHuntSource',  TRUE, 2, 'RSS,无投票数据,Top 20 配额 2 条留窄趋势窗口');


-- ------------------------------------------------------------
-- 2) topics — 主题(active/archived,hard switch)
--    Anna 设 active topic,系统按 active topic 跑 routine;
--    切换 = 旧 active → archived,新主题 → active(同一时刻最多 1 个 active)
-- ------------------------------------------------------------
CREATE TABLE topics (
  topic_id      BIGSERIAL PRIMARY KEY,
  keyword       TEXT NOT NULL,                  -- 用户输入的主题词,如 'AI 创业'
  status        TEXT NOT NULL CHECK (status IN ('active', 'archived')),
  created_by    TEXT,                           -- anna / junxi / carrie
  started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  archived_at   TIMESTAMPTZ,
  notes         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- status 与 archived_at 绑定:active 必须未归档,archived 必须有归档时间
  CHECK (
    (status = 'active'   AND archived_at IS NULL) OR
    (status = 'archived' AND archived_at IS NOT NULL)
  )
);

-- 约束:同一时刻最多 1 个 active topic(partial unique index)
CREATE UNIQUE INDEX uq_topics_one_active ON topics (status) WHERE status = 'active';

CREATE INDEX idx_topics_status ON topics (status);
CREATE INDEX idx_topics_started_at ON topics (started_at DESC);


-- ------------------------------------------------------------
-- 3) topics_cache — 主题 → subreddit 映射缓存
--    PRD §4 第 4 步:7 天 TTL + cached_at + stale flag + 30 天 hard ceiling
-- ------------------------------------------------------------
CREATE TABLE topics_cache (
  cache_id          BIGSERIAL PRIMARY KEY,
  topic_keyword     TEXT NOT NULL UNIQUE,       -- 缓存 key
  subreddits        JSONB NOT NULL,             -- [{name, relevance_score, quality_score, source: search/llm/synonym}]
  allow_list_applied JSONB,                     -- 应用了哪些白名单条目
  deny_list_applied  JSONB,                     -- 应用了哪些黑名单条目
  cached_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  expires_at        TIMESTAMPTZ NOT NULL,       -- cached_at + 7 days(TTL)
  hard_ceiling_at   TIMESTAMPTZ NOT NULL,       -- cached_at + 30 days(超此视作 cache miss)
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_topics_cache_expires ON topics_cache (expires_at);


-- ------------------------------------------------------------
-- 4) operator_lists — allow/deny list(operator 兜底层)
--    PRD §4 第 3 步:白名单强制纳入 / 黑名单永久剔除
--    scope_topic_id = NULL → 适用全局所有主题
--    scope_topic_id 指向某 topic → 只对该主题生效
-- ------------------------------------------------------------
CREATE TABLE operator_lists (
  list_id        BIGSERIAL PRIMARY KEY,
  list_type      TEXT NOT NULL CHECK (list_type IN ('allow', 'deny')),
  subreddit_name TEXT NOT NULL,                 -- 'OpenAI' / 'funny' / 等
  scope_topic_id BIGINT REFERENCES topics(topic_id),  -- NULL = 全局
  notes          TEXT,
  created_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_operator_lists_scope ON operator_lists (scope_topic_id, list_type);


-- ------------------------------------------------------------
-- 5) runs — 每次"跑"的 metadata(cron 触发 or 用户手动触发)
--    Q3 拍定:每天独立 run + archive 累积,run 是分析单元
-- ------------------------------------------------------------
CREATE TABLE runs (
  run_id              BIGSERIAL PRIMARY KEY,
  topic_id            BIGINT NOT NULL REFERENCES topics(topic_id),
  topic_keyword       TEXT NOT NULL,            -- denormalized,方便查询
  triggered_by        TEXT NOT NULL CHECK (triggered_by IN ('cron', 'manual')),
  triggered_by_person TEXT,                     -- anna / junxi / carrie / 'system' (cron)
  status              TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at         TIMESTAMPTZ,
  posts_count         INT,                      -- 过完三闸门后的全集条数(30-60)
  top20_count         INT,                      -- 实际进 MD 日报的条数(可能 <20 因跨日去重)
  ai_mode             TEXT CHECK (ai_mode IN ('ai', 'heuristic')),  -- AI 点评模式
  sanity_status       TEXT CHECK (sanity_status IN ('OK', 'OK_WITH_ANOMALY', 'FAIL')),
  sanity_anomalies    JSONB,                    -- 异常项列表(若有)
  config_fingerprint  TEXT NOT NULL,            -- 此次跑的配置指纹(系统③ V2 校准分组用)
  error_message       TEXT,                     -- 若 failed,错误信息
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_runs_topic_id ON runs (topic_id);
CREATE INDEX idx_runs_started_at ON runs (started_at DESC);
CREATE INDEX idx_runs_status ON runs (status);
CREATE INDEX idx_runs_fingerprint ON runs (config_fingerprint);


-- ------------------------------------------------------------
-- 6) posts_archive — 帖子全集累积表
--    Cindy must-reserve #1: (source, source_native_id) UNIQUE 防重 + post_id PK;
--    starred / report_top20 用 post_id FK
--    Richard 必备: full_content 不存这里,放 Supabase Storage(full_content_url)
-- ------------------------------------------------------------
CREATE TABLE posts_archive (
  post_id            BIGSERIAL PRIMARY KEY,
  -- 来源标识(防同一帖被存两遍)
  source             TEXT NOT NULL REFERENCES sources(source_key),
  source_native_id   TEXT NOT NULL,             -- 来源原生 ID,如 reddit '1tgu8va'
  -- 内容
  title              TEXT NOT NULL,
  url                TEXT NOT NULL,
  raw_snippet        TEXT,                      -- ≤500 chars 摘要(轻量,留 DB 直接查询用)
  full_content_url   TEXT,                      -- 指向 Supabase Storage 里的全文 .json.gz 文件
  -- 互动 metrics
  raw_metrics        JSONB,                     -- {likes, comments, crossposts}
  -- 打分
  hot_score          REAL,                      -- 0-100,归一化
  relevance_score    REAL,                      -- 0-1
  -- 三层标签(PRD §9)
  tags_json          JSONB,                     -- {domain: [...], entity: [...], intent: '...'}
  -- AI 点评(PRD §10.2.2)
  ai_review          JSONB,                     -- {xhs_title, comment, tier (强/中/弱), mode (ai/heuristic)}
  comments_summary   JSONB,                     -- top 8-10 评论摘要(玩梗二判用 + 写稿参考)
  -- 时间
  published_at       TIMESTAMPTZ,
  fetched_at         TIMESTAMPTZ,
  -- 关联
  run_id             BIGINT NOT NULL REFERENCES runs(run_id),
  config_fingerprint TEXT NOT NULL,             -- 必带(系统③ V2 校准用)
  -- 元
  source_native      JSONB,                     -- {subreddit, flair, permalink, ...} - 原生 metadata
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  -- 约束
  UNIQUE (source, source_native_id)             -- 防同一帖跨多次 run 被存多遍
);

CREATE INDEX idx_posts_archive_run_id ON posts_archive (run_id);
CREATE INDEX idx_posts_archive_source ON posts_archive (source);
CREATE INDEX idx_posts_archive_hot_score ON posts_archive (hot_score DESC);
CREATE INDEX idx_posts_archive_published_at ON posts_archive (published_at DESC);
CREATE INDEX idx_posts_archive_fetched_at ON posts_archive (fetched_at DESC);
CREATE INDEX idx_posts_archive_fingerprint ON posts_archive (config_fingerprint);
-- 给 tags_json 留 GIN index 以备未来按 entity/intent 检索(系统②/③ 用)
CREATE INDEX idx_posts_archive_tags_gin ON posts_archive USING GIN (tags_json);


-- ------------------------------------------------------------
-- 7) report_top20 — 每次 run 的 Top 20 报告(前端"今日报告"渲染源)
--    PRD §10.2 给主编 review + star 的入口
-- ------------------------------------------------------------
CREATE TABLE report_top20 (
  report_id   BIGSERIAL PRIMARY KEY,
  run_id      BIGINT NOT NULL REFERENCES runs(run_id),
  post_id     BIGINT NOT NULL REFERENCES posts_archive(post_id),
  rank        INT NOT NULL CHECK (rank BETWEEN 1 AND 20),  -- Top 20(可能 <20 因跨日去重)
  tier        TEXT CHECK (tier IN ('强', '中', '弱')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (run_id, rank),
  UNIQUE (run_id, post_id)   -- 同一帖在同一 run 报告里只能出现一次(防上游去重 bug 导致重复条目)
);

CREATE INDEX idx_report_top20_run_id ON report_top20 (run_id);


-- ------------------------------------------------------------
-- 8) starred — 主编精选库(per-人,soft delete)
--    Cindy must-reserve #1+#4: post_id FK(不 dup data)+ deleted_at NULLABLE
--    系统② 写稿优先消费;系统③ 分析主编偏好
-- ------------------------------------------------------------
CREATE TABLE starred (
  star_id     BIGSERIAL PRIMARY KEY,
  person      TEXT NOT NULL,                    -- 'anna' / 'junxi' / 'carrie'
  post_id     BIGINT NOT NULL REFERENCES posts_archive(post_id),
  run_id      BIGINT REFERENCES runs(run_id),   -- 在哪次 run 里 star 的(可空,如批量补 star)
  starred_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at  TIMESTAMPTZ,                      -- soft delete(取消 star 时填入)
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 约束:同一 person 对同一 post 同一时刻只能有 1 个 active star
-- (soft delete 历史保留,允许 unstar 后再 star)
CREATE UNIQUE INDEX uq_starred_active ON starred (person, post_id) WHERE deleted_at IS NULL;

CREATE INDEX idx_starred_person ON starred (person);
CREATE INDEX idx_starred_post_id ON starred (post_id);
CREATE INDEX idx_starred_starred_at ON starred (starred_at DESC);


-- ------------------------------------------------------------
-- 9) suggested_keywords — 关键词词表生长追踪(PRD §5.1.2)
--    AI 抽出的高频实体若不在 keywords.yaml,自动登记给 operator 月度回顾
-- ------------------------------------------------------------
CREATE TABLE suggested_keywords (
  id                 BIGSERIAL PRIMARY KEY,
  tag_layer          TEXT NOT NULL CHECK (tag_layer IN ('domain', 'entity')),
  tag_value          TEXT NOT NULL,
  occurrence_count   INT NOT NULL DEFAULT 1,
  first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  reviewed           BOOLEAN NOT NULL DEFAULT FALSE,    -- operator 审过没
  reviewed_decision  TEXT CHECK (reviewed_decision IN ('add', 'reject')),  -- NULL 自动允许(未审);列表里不放 NULL,否则非法值会漏过约束
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (tag_layer, tag_value)
);

CREATE INDEX idx_suggested_keywords_reviewed ON suggested_keywords (reviewed);
CREATE INDEX idx_suggested_keywords_last_seen ON suggested_keywords (last_seen_at DESC);


-- ============================================================
-- 触发器: 自动维护 updated_at
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 给所有有 updated_at 的表挂上触发器
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
-- 完成. 9 张表 + 索引 + 约束 + 触发器.
-- ============================================================
