# 系统① Dev Plan —— 完整技术版

> lil-Anna 维护的工程权威文档。**这是内部技术文档,保留所有术语和实现细节**(与「界面文案大白话」相反)。
> 流程:每步 lil-Anna 写 → @Rex(代码审查 agent)独立审 → 修 → Rex 复审 → 交 Anna 拍 → 下一步。
> 代码根目录:`~/Projects/xhs-ai-ip/system1-app/`。Legacy production:`~/Projects/xhs-ai-ip/system1-scraper/`。

---

## 0. 架构概览
统一 web 应用,分阶段做(系统①热点抓取 → ②写稿 → ③分析,后两者后续并入)。主题驱动 routine 模式:Anna 设 active topic → on-demand / cron 跑 pipeline → 出 Top20 报告 → 主编 review + star。

数据流:
```
topic(keyword)
  → topic_mapping(Step3): 候选生成→打分→operator 闸→缓存 → subreddit 列表
  → sources(Step2): RedditSource / ProductHuntSource .fetch() → List[HotItem]
  → scoring(Step4): hot_score 归一化 + relevance_score
  → filter_hot: 三闸门(relevance≥thr ∧ Top hot_top_percent% ∧ ≥ floor)
  → merge: dedup_items(dedup_key) + enrich_tags
  → select_ranked: PH 配额 + 全局 hot 补位 → Top-N
  → ai_review: 强/中/弱 tier + 点评(heuristic 占位 / 真 LLM=Step6)
  → sanity_check → RunResult (posts~posts_archive, top~report_top20)
  → [Step6] 写 Supabase → [前端] 渲染
```

## 1. 技术栈(精确)
| 层 | 选型 | 版本/细节 |
|---|---|---|
| 前端 | Next.js (Pages Router) + React + Tailwind | Next 16.2.6 / React 19.2.4 / Tailwind v4(`@theme` CSS tokens,无 tailwind.config.js)/ TS |
| 后端主线 | Python pipeline | `pipeline/` 包;stdlib + `requests` |
| LLM | OpenAI GPT-4o-mini(默认) | AI 点评 + 标签;走 `api.openai.com` 直连(**不走 Slock 代理 127.0.0.1:7878,代理对 OpenAI 401**) |
| DB | Supabase (PostgreSQL) | 免费档 500MB;migration `supabase/migrations/0001_init.sql` |
| 大文件 | Supabase Storage | full_content 压缩 JSON,DB 只存 `full_content_url` |
| 部署 | Vercel | Hobby + Fluid Compute(300s timeout, scale-to-zero) |
| 密钥 | Vercel env / 本地 .env | OpenAI / Supabase / (Reddit) —— 不进 repo |

## 2. 仓库结构(system1-app/)
```
supabase/migrations/
  0001_init.sql                       # Step1 schema(9 表)
  0002_report_review_fields.sql       # Step6② per-run comment/xhs_title 列
  0003_switch_active_topic.sql        # Step7 主题硬切换 plpgsql 事务 RPC
docs/{schema.md, DEV_PLAN.md, REVIEW_CHARTER.md}
pipeline/                              # Python 主线
  schema.py                            # HotItem 数据契约 + helper
  sources/{base, registry, reddit_source, product_hunt_source, xiaohongshu_source}.py
  topic_mapping.py                     # Step3 主题映射算法
  topic_resolve.py                     # Step3 落地补完(LLM 版块+关键词,绕 §7)
  scoring.py merge.py config.py ai_review.py runner.py   # Step4 主线
  store.py supa.py                     # Step6① Supabase 写入 + client
  run_once.py                          # Step6② Node 子进程入口
  tests/{test_topic_mapping(17), test_runner(9), test_store(9), test_topic_resolve(3)}.py
web/                                   # Step5/6/7 Next.js 前端
  pages/index.tsx pages/_document.tsx pages/_app.tsx
  pages/api/{run, run/[id], runs, star, starred, topics}.ts
  components/{Sidebar, ReportList, RunTab, StarredTab, TopicPanel, SettingsTab}.tsx
  lib/{types, supabase-server, api, report-mapping}.ts
  styles/globals.css  .env.local(gitignored)
preview/build_preview.py preview.html  # 暖色设计预览生成器
experiments/{firecrawl,apify}/         # 调研 spike
```

---

## 3. 八步详解

### Step 1 — 数据库 schema ✅ Rex 审过
- **文件**:`supabase/migrations/0001_init.sql`(276 行)+ `docs/schema.md`(Mermaid ER 图)。
- **9 张表**:`sources`(数据源注册:source_key UK / adapter_class / quota_top20)、`topics`(keyword / status active|archived / archived_at)、`topics_cache`(topic_keyword UK / subreddits JSONB / cached_at / expires_at / hard_ceiling_at)、`operator_lists`(list_type allow|deny / subreddit_name / scope_topic_id FK NULL=全局)、`runs`(topic_id FK / triggered_by cron|manual / status / ai_mode / sanity_status / config_fingerprint)、`posts_archive`(post_id PK / source FK→sources.source_key / source_native_id / tags_json / ai_review JSONB / full_content_url / config_fingerprint / **UNIQUE(source, source_native_id)**)、`report_top20`(run_id FK / post_id FK / rank / tier / **UNIQUE(run_id,rank) + UNIQUE(run_id,post_id)**)、`starred`(person / post_id FK / deleted_at 软删 / **partial UNIQUE(person,post_id) WHERE deleted_at IS NULL**)、`suggested_keywords`(tag_layer / tag_value / occurrence_count / **UNIQUE(tag_layer,tag_value)**)。
- **约束/索引**:`uq_topics_one_active`(partial UNIQUE ON topics(status) WHERE status='active' → 同时刻最多 1 active)、GIN index on `posts_archive.tags_json`、`updated_at` 触发器(plpgsql `trigger_set_updated_at` 挂 8 表)。
- **5 must-reserve**(Cindy+Richard):(source,source_native_id) UNIQUE + post_id PK / config_fingerprint 必带 / 全 TIMESTAMPTZ(UTC)/ soft delete deleted_at / full_content 走 Storage。
- **Rex 审出 bug(已修)**:① `report_top20` 缺 `UNIQUE(run_id, post_id)`(同帖可占多 rank)② `topics` 缺 status↔archived_at 一致性 CHECK ③ `rank` 改 `CHECK (rank BETWEEN 1 AND 20)` ④(自查 pglast)`suggested_keywords.reviewed_decision CHECK ... IN ('add','reject',NULL)` 的 NULL 让非法值漏过 → 去掉 NULL。
- **验证**:pglast(libpg_query)解析全文件 35 语句通过。
- **未做**:跑进真 Supabase(Step8 部署时)。

### Step 2 — 数据源插件层 ✅ Rex 审过
- **文件**:`pipeline/schema.py` + `pipeline/sources/`。
- **HotItem dataclass**(`schema.py`):id / dedup_key / title / source / **source_native_id**(对齐 posts_archive UNIQUE)/ url / published_at / captured_at / raw_metrics{likes,comments,saves,upvotes} / source_native / hot_score / relevance_score / tags / raw_snippet。helper:`make_id(source,native_id)`(sha1)、`canonical_url`(剔 utm_/fbclid/ref…)、`clip_snippet`(≤500)。
- **可插拔架构**:`Source` ABC(`base.py`,`fetch()->List[HotItem]`)+ `registry.py`(`SOURCE_REGISTRY: source_key→adapter 类` + `get_source()` / `build_sources()`)。加新源 = 写 adapter + registry 注册 + sources 表 INSERT,主线零改。
- **adapters**:`RedditSource`(public 匿名 .json / oauth client_credentials 两模式;UA 校验;指数退避 + Retry-After;failed_subs)、`ProductHuntSource`(rss Atom / token GraphQL 两模式)、`XiaohongshuSource`(stub,raise NotImplementedError)。
- **Rex 审出 bug(已修)**:`product_hunt_source._fetch_token` 把 GraphQL 200+errors 当空结果**静默失败** → 改成检查顶层 `errors`/缺 `data.posts` 就 raise → fetch() 置 failed=True。+ adapter 缺 native_id/url 跳过守卫。
- **验证**:compileall + 7 项 smoke(registry/factory/stub 抛 NIE/未知源 KeyError/build_sources/HotItem.to_dict)。

### Step 3 — 主题映射 4 步算法 ✅ Rex 审过
- **文件**:`pipeline/topic_mapping.py` + `tests/test_topic_mapping.py`(17 测)。
- **4 步**:① 候选生成(`reddit_search_fn` 版块搜索 + `llm_suggest_fn` 推荐/同义词,都可注入;LLM 失败 fail-soft 降级)② 打分 `score = 0.65*relevance + 0.35*quality`(relevance 由搜索位置+关键词重合;quality 由订阅数 log 归一,未知=0.5)③ operator 闸:allow/deny(deny 优先,allow 强制纳入 score=1.0)+ 边缘 case 告警 ④ 缓存:7d TTL(expires_at)/ 30d hard_ceiling / `--no-cache`。
- **缓存不变量**:hard_ceiling 只首次派生/超限时设,**TTL 续期不往后推**(`_carry_hard_ceiling`)。
- **Rex 审出 bug(已修)**:缓存原本存 operator 后的最终结果 → **跨调用/跨 topic 污染 operator 决策**。重构:**缓存只存纯候选池**(`_generate_and_score`),operator 每次调用重套(`_finalize` / `_finalize_from_cache`)。+ 落地 stale fallback(派生失败且 hard_ceiling 内 → 回退 stale 缓存;超限 fail loud)。
- **实证**:`default_reddit_search("AI")` 实跑 → 403 Blocked(Reddit 匿名被限,见 §7)。

### Step 4 — 主线本地端到端整合 ✅ Rex 审过
- **文件**:`pipeline/{runner,scoring,merge,config,ai_review}.py` + `tests/test_runner.py`(9 测)。
- **scoring.py**(从 legacy 移植,M1 FROZEN 不改):`hot_score = (w_like*likes + w_comment*comments + w_saveshare*saves) * 0.5^(age_h/half_life)`,按来源内 max 归一化 0–100;`relevance` = 命中不同关键词数 / relevance_full_hit,封顶 1.0;`filter_hot` 三闸门。
- **merge.py**:`dedup_items`(dedup_key 合并,保 hot 高者,平局按源优先级)、`enrich_tags`(原生 + kw: 前缀,≤max_tags)、`select_ranked`(source_quota 保底 + recency + 全局 hot 补满)。
- **config.py**:`DEFAULT_CONFIG` —— scoring{1,1,1,half_life 48} / filter{relevance_threshold 0.5, relevance_full_hit 2, hot_top_percent 20, min_absolute_hot_score 2.0} / merge{dedup, dedup_source_priority, source_quota **{product_hunt:2}**(加固#3)} / output{daily_top_n 20, store_top_n 50} / DEFAULT_KEYWORDS(30 词)。
- **runner.py**:`run_pipeline(topic, sources, *, cfg, keywords, review_fn, triggered_by, now) -> RunResult`;`config_fingerprint()`(cfg+词表 sha1,盖每条 source_native);`sanity_check()`(empty / count<10 / ai_degraded / **ai_meta_missing** / source_skew>75% / source_fetch_failed);`build_topic_sources()`(主题映射→源,**cfg 驱动**)。`RunResult`{topic,run_at,status,config_fingerprint,candidates_count,scored_count,posts,top,ai_mode,sanity,failed_sources}。
- **ai_review.py**:`heuristic_review(items,cfg)->(meta,mode)` 占位(按 hot 分位分强/中/弱);真 LLM 同接口注入(Step6)。
- **Rex 审出 bug(已修)**:`build_topic_sources` 硬编码 PH `auth_mode='rss'` 忽略 cfg → 改 cfg 驱动。+🟡 ai_meta_missing sanity guard +🟡 全源失败 status=failed。
- **状态**:stub 数据离线跑通;真实 Reddit/OpenAI/Supabase 联调 = Step6。

### Step 5 — Next.js 前端骨架 ✅ Rex 审过
- **文件**:`web/`(Pages Router + TS + Tailwind v4)。
- **结构**:`pages/index.tsx`(app 壳:`tab` 状态 + `starred: Set<string>` + `toggle(id)`;import `data/sample-report.json as Report`)、`components/`(Sidebar 4 tab / ReportList 分档+Row+☆ / RunTab / StarredTab / TopicsTab / SettingsTab)、`lib/types.ts`(ReportItem{**id**,rank,title,tier_*,source,likes,comments,url,comment} / Report / tierColor)、`styles/globals.css`(暖色 `@theme` tokens:cream/panel/line/ink/mut/terra/strong/mid/weak)。
- **设计**(Anna 拍):暖色调 + 一连串列表(非卡片框)+ **界面文案全大白话,无技术术语**。
- **Rex 审出 bug(已修)**:收藏/React key 原用 `rank`(排序位置,非身份)→ 跨天/换主题会串 → 改用稳定 `id`(URL 派生 hash;sample 数据补 id)。+ 文案收窄 / `as Report` 注释标 Step6 校验。
- **验证**:`npm run build` 通过;`npm run dev` 在 :3000(后台 nohup,/tmp/system1-web-dev.log)。`preview/build_preview.py` 从 daily_report.md 生成单文件 HTML 预览(headless Chrome 可截图)。

### Step 6 — API 路由 + 连 Supabase ✅ 全过审(2026-05-25/26)
- **Supabase 已建库**:Anna 2026-05-25 开免费项目(ref ksesknktnwtxexlqtivb),`0001_init.sql` 经 SQL Editor 跑进去(9 表 + seed)。后续追加两条 migration(也走 SQL Editor):**`0002_report_review_fields.sql`**(report_top20 加 `comment` + `xhs_title` per-run 点评列)、**`0003_switch_active_topic.sql`**(plpgsql 事务 RPC 硬切换主题)。Creds 在 `system1-app/.env`(600,gitignore)。直连 DDL 从本机不可达(IPv6/pooler)→ DDL 走 SQL Editor;数据走 PostgREST(secret key)。
- **① 数据层 ✅ Rex 过审**:`pipeline/store.py`(`runresult_to_rows` 纯映射 + `SupabaseStore` 注入 client;topic→run→posts_archive→report_top20;精选库 add/remove 软删)+ `pipeline/supa.py`(get_client from .env)+ `tests/test_store.py`(9 测)。**Rex 第一轮 🔴**:① posts_archive 原 blanket upsert 覆写历史 → 改 **append-only**(查已存在→只 insert 新;`save(res, topic_id=None)` 支持显式传);② ensure_topic 与 `uq_topics_one_active` 冲突 → 保守(同 keyword 复用 active / 别的 active **fail-loud** / 无 active 才建);+🟡 .env 解析去引号、get_starred 加 `order(starred_at desc)`。
- **② API + 前后端接真 ✅ Rex 过审**:
  - **Node 端**:`web/lib/supabase-server.ts`(server-only 懒构建)/ `lib/api.ts` 方法守卫 / `lib/report-mapping.ts`(DB→ReportItem **runtime 边界校验**,收 `as Report` 🟡)。`pages/api/`={`run.ts`(POST,Node spawn `python -m pipeline.run_once`,argv 非 shell,180s kill,解析末行 JSON)/ `run/[id].ts`(GET,id=latest|num,report_top20 join posts_archive,**latest 严格按 active topic 取**)/ `runs.ts` / `star.ts`(POST/DELETE 软删,23505 幂等)/ `starred.ts` / `topics.ts`(GET + POST **硬切换走 RPC**)}。
  - **Python 端**:`pipeline/run_once.py`(Node 子进程入口,加载 .env→resolve_topic→build_sources→run_pipeline→save;stdout 末行 JSON;pipeline 日志重定向 stderr)+ `pipeline/ai_review.py` 新增 `openai_review`(gpt-4o-mini api.openai.com **直连,`requests.Session(trust_env=False)` 绕 Slock 代理**)+`select_review_fn()`(有 OPENAI_API_KEY → 真 LLM,否则 heuristic;**任何失败整体回退 heuristic**,收 deferred「LLM 全失败回退」)。
  - **配置**:`web/.env.local`(SUPABASE_URL+SECRET_KEY,gitignore 600)/ `.env` 加 `REDDIT_USER_AGENT`(匿名 public Reddit 通了)。
  - **Rex 第二轮 🔴**:① comment 历史漂移(从 append-only posts_archive 读)→ per-run comment/xhs_title 落 `report_top20`(**migration 0002**;threaded:openai_review meta + runner top + store + mapping + select)。② 前端 POST /api/run 后读 `r.run_id` 而非 `/latest`(防并发/cron 串)。+🟡 types ReportItem.id 过期注释、loadReport/loadStarred 加 res.ok + loadError 提示。
- **真 AI live(Anna 2026-05-25 给 OpenAI key)**:从 `system1-scraper/.env` 把 `OPENAI_API_KEY` 带到 `system1-app/.env`,直连。run 11 真跑 ai_mode=ai,**20/20 中文 xhs_title + 中文点评**落 report_top20。
- **已收 deferred**:✅ id→post_id / ✅ `as Report`→边界校验 / ✅ LLM 全失败回退 heuristic / ✅ Reddit 匿名 public 通(UA 修)/ ✅ OpenAI 直连。**遗留(Step8)**:Vercel NFT warning(run.ts path.resolve);trust_env=False 部署到企业代理/定制 CA 环境时要重验。

### Step 7 — 前端 Tab 1+2 完整 🚧 4 项过审 / 2 项剩(2026-05-26)
**✅ 已过审(4 项)**:
- **强迁移置顶**:ReportList tier 段按 **强→中→弱 优先级排**(原按"首次出现"顺序,真 AI 分档下强迁移会跑到中下面)。Anna 实测发现。
- **版块显示**:TopicPanel 右侧栏 "本次内容来自:r/X · r/Y · …"(从 report.items.source 去重,RunTab 算后传)。
- **加固#2 新/老帖重现**:posts_archive 的 run_id(首次入库 run)对比当前 run → `is_new`;Row 显 "🔁 老帖重现" badge;RunTab 头 "X 新 / Y 重现" 计数。types/mapping/run-[id] 联动。
- **主题管理(右侧栏 + 硬切换)**:Anna 让把单独"主题"tab 去掉、挪 RunTab 右侧 → `TopicPanel.tsx`(新)+ RunTab 双栏 + Sidebar 去"主题" + types TabKey 收窄 + 删 TopicsTab + main 宽 3xl→5xl。后端硬切换 = `pages/api/topics.ts` POST 改调 **`switch_active_topic` plpgsql 事务 RPC**(migration 0003,归档旧 active → 复用/新建目标,事务回滚保证恒 1 active);**Rex 🔴**:原 3 次独立 PostgREST 调用非事务,中途失败会留 0 active → 改 RPC。verified live:切全新无 run 主题 → latest null 不串;切回复用;no-op;全程恰好 1 active。

**Step 3 落地 补完(2026-05-26,Anna 测出"切主题内容不变"→ 触发)**:
- 根因:Step 3 的 `topic_mapping.py` 是 Rex 过审的算法,但**没接到真跑**——它需要"去 Reddit 搜版块",而 Reddit 匿名搜索一直不稳(原 gated 在 §7),run_once 顶替用了写死 DEFAULT_SUBREDDITS + DEFAULT_KEYWORDS。
- **新增 `pipeline/topic_resolve.py`**:绕开 Reddit 搜索,用 LLM(gpt-4o-mini)推荐版块 + 单独一次 LLM 调用给 per-topic 英文相关性词;**复用 Step 3 TopicMapper 的 LLM 路径**(`reddit_search_fn=_noop`,`llm_suggest_fn=_llm_subreddits`,target=2× 后过验证再砍);**`_verify_subreddit` 每个 ping `r/X/about.json` 剔 404 幻觉**(404=确认不存在;其它=保留,留给主抓取再试);早停到 target_count;返回分开的 **`subreddits_source` / `keywords_source`**(Rex 🔴:不能合并成一个 source,否则"LLM 版块成功+关键词回退"会被误打回严闸)。
- **`run_once.py`**:`mapping=resolve_topic(args.topic, DEFAULT_SUBREDDITS, cfg["keywords"])` → `build_sources(cfg, mapping["subreddits"])` → `run_pipeline(..., keywords=mapping["keywords"])`。**`subreddits_source=='llm'` 时 `relevance_threshold` 改 0**(版块本身是话题过滤,松闸防"Taylor Swift"标题没"celebrity"字面被错杀)。
- 新增 `tests/test_topic_resolve.py`(3 测:无 key→两 fallback / LLM subs OK + keywords fail 仍标 subs llm / partial 404 保留 verified 不回退)。
- **Rex 过审**(2 轮:第一轮 🔴 source 合并;修了 → 复审通过)。live celebrity:LLM 10 候选 → 验证剔 6 幻觉(r/CelebrityNews 等)→ 留 5 真版块 → 31-37 posts / 20 top / ai_mode=ai,**真名人内容(Sydney Sweeney/安妮·海瑟薇/Aubrey Plaza)**。

**🚧 Step 7 剩 2 项**:① 精选库跨 run 筛选(person / 来源)② MappingResult.notes 结构化字段(is_stale/warnings[],Step3 原🟡)。**等 Anna 测试 Step 7 完成的 4 项 + 定下一步。**

### Step 8 — Vercel 部署 + cron(需 Anna 给 GitHub/Supabase/Vercel 账号)
- GitHub repo + Vercel 自动部署 + env 密钥 + 每日 cron(09:00 LA)+ 历史 8 天 SQLite 是否搬 Supabase(加固#5,待 Anna 拍)。

---

## 4. 实战加固点(5/17–5/24 8 天实战,已并入)
背景:日报持续缩水(20→18→17→17→15),根因 = 跨天去重(reported_ledger)+ 固定版块 → 新帖越捞越少。
| # | 加固点 | 落点 | 状态 |
|---|---|---|---|
| 1 | 主题枯竭出口(连续几天新帖<N→提示换主题,接 hard switch) | Step3/4→7 | 待 |
| 2 | 日报标「X 新 / Y 老帖重现」 | Step7 | 待 |
| 3 | PH 配额=2(source_quota) | Step4 | ✅ 已落 config |
| 4 | 稳健性(重试退避/失败源不静默/sanity/AI 回退 heuristic) | 贯穿 | 进行中(Step2/4 已落部分) |
| 5 | 历史 8 天 SQLite 搬不搬 Supabase | Step8 前 | 待 Anna 拍 |

## 5. Reddit 取数策略(2026,Step 6 要定)
- Reddit 2025-11 **Responsible Builder Policy**:关停自助 API key,新 app 要 Developer Support 表人工审批(~7天,常被拒)。.env 里 REDDIT_CLIENT_ID/SECRET 是占位符(从没真用 OAuth)。
- 免费访问强制 OAuth token;匿名/未认证被随意限流/封 = 间歇 403(实测:早 06:00 cron 行、下午同端点 403)。
- **三路(待 Anna/Junxi 拍)**:A 官方申请(慢,建议 Junxi 提)/ B 匿名+加固(现用,适配器带重试)/ C 第三方 Apify Reddit actor(trudax-lite $3.4/1k 等,我们 ~1.05万/月 ≈ $16-36/月)。**建议 B 顶 + 评估 C 兜底 + A 并行。** 插件式,接入方式藏 RedditSource 后,主线不改。

## 6. Deferred / 技术债(状态截止 2026-05-26)
- ✅ LLM 全失败自动回退 heuristic + mode=heuristic(Step6② `select_review_fn` + `openai_review` 异常回退)
- ✅ 前端 id → 真 posts_archive.post_id(Step6① 接 DB 后用真主键)
- ✅ `as Report` → 边界校验(Step6② `lib/report-mapping.ts` runtime 校验)
- 🚧 MappingResult.notes → 结构化字段 is_stale/warnings[](Step7 剩)
- 🚧 PH token 成功路径真实 smoke(需 PH developer token,长期低优)
- 🚧 Vercel NFT warning(run.ts `path.resolve(process.cwd(),"..")`,留 Step8 部署再处理;`trust_env=False` 在企业代理/定制 CA 环境要重验)
- 🚧 Reddit OAuth(§7,Anna 选申请慢→暂走匿名 public+UA 修;Apify 兜底待评估)

## 7. 测试覆盖
- `test_topic_mapping.py` 17 测(排序/LLM 降级/allow-deny/缓存命中/TTL 过期/hard ceiling 不推后/operator 不跨调用污染/topic scope 不泄漏/stale fallback/--no-cache 等)
- `test_runner.py` 9 测(端到端/PH 配额露出/失败源隔离/空源/fingerprint 稳定/relevance 闸/build_topic_sources cfg/全源失败 status=failed/ai_meta_missing)
- `test_store.py` 9 测(runresult_to_rows shape/save 用真 post_id/tier 映射短名/star 软删/append-only 跨 run/insert 只新/ensure_topic 复用 active/fail-loud 别 active/per-run comment 落 report_top20)
- `test_topic_resolve.py` 3 测(无 key→fallback / LLM subs OK + keywords fail 仍标 llm / partial 404 保留 verified 不回退)
- 前端:`npm run build` + `npm run lint`(TS + 编译 + 静态生成 + eslint)
- schema:pglast 解析(Step1 自查)
- live 真库验证:每修一处 Rex 提的真问题都 live 复现 + 断言(append-only drift / per-run comment drift / 切主题 latest 不串 / 硬切换全程 1 active 等)
- **审查闭环**:Step 1-6 + Step 7 已过审项 + Step 3 落地补完,每步 Rex 找出真 bug → 修 → 复审通过(累计 修 9 个 🔴 + 十几个 🟡)。
