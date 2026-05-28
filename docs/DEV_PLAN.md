# System ① Dev Plan — Full Technical Edition

> Authoritative engineering doc maintained by lil-Anna. **This is the internal technical doc — all terminology and implementation detail is preserved** (the opposite of "plain-language UI copy").
> Workflow: each step lil-Anna writes → @Rex (code-review agent) reviews independently → fixes → Rex re-reviews → Anna signs off → next step.
> Code root: `~/Projects/xhs-ai-ip/system1-app/`. Legacy production: `~/Projects/xhs-ai-ip/system1-scraper/`.

---

## 0. Architecture overview
A unified web app, shipped in phases (System ① topic discovery → ② drafting → ③ analysis; the latter two merge in later). Topic-driven routine: Anna sets an active topic → on-demand / cron runs the pipeline → produces a Top-20 report → chief editor reviews + stars.

Data flow:
```
topic(keyword)
  → topic_mapping(Step3): candidate generation → scoring → operator gate → cache → subreddit list
  → sources(Step2): RedditSource / ProductHuntSource .fetch() → List[HotItem]
  → scoring(Step4): hot_score normalization + relevance_score
  → filter_hot: three gates (relevance ≥ thr ∧ Top hot_top_percent% ∧ ≥ floor)
  → merge: dedup_items(dedup_key) + enrich_tags
  → select_ranked: PH quota + global hot top-up → Top-N
  → ai_review: strong/medium/weak tier + critique (heuristic placeholder / real LLM = Step6)
  → sanity_check → RunResult (posts ~ posts_archive, top ~ report_top20)
  → [Step6] write to Supabase → [frontend] render
```

## 1. Stack (precise)
| Layer | Choice | Version / detail |
|---|---|---|
| Frontend | Next.js (Pages Router) + React + Tailwind | Next 16.2.6 / React 19.2.4 / Tailwind v4 (`@theme` CSS tokens, no tailwind.config.js) / TS |
| Backend pipeline | Python pipeline | `pipeline/` package; stdlib + `requests` |
| LLM | OpenAI GPT-4o-mini (default) | AI review + tagging; direct to `api.openai.com` (**not via Slock proxy 127.0.0.1:7878 — the proxy 401s on OpenAI**) |
| DB | Supabase (PostgreSQL) | Free tier 500MB; migration `supabase/migrations/0001_init.sql` |
| Large blobs | Supabase Storage | full_content compressed JSON; DB only holds `full_content_url` |
| Deployment | Vercel | Hobby + Fluid Compute (300s timeout, scale-to-zero) |
| Secrets | Vercel env / local .env | OpenAI / Supabase / (Reddit) — never committed |

## 2. Repo layout (system1-app/)
```
supabase/migrations/
  0001_init.sql                       # Step 1 schema (9 tables)
  0002_report_review_fields.sql       # Step 6② per-run comment/xhs_title columns
  0003_switch_active_topic.sql        # Step 7 atomic topic-switch plpgsql RPC
docs/{schema.md, DEV_PLAN.md, REVIEW_CHARTER.md}
pipeline/                              # Python pipeline
  schema.py                            # HotItem data contract + helpers
  sources/{base, registry, reddit_source, product_hunt_source, xiaohongshu_source}.py
  topic_mapping.py                     # Step 3 topic-mapping algorithm
  topic_resolve.py                     # Step 3 landing layer (LLM subs + keywords, bypasses §7)
  scoring.py merge.py config.py ai_review.py runner.py   # Step 4 pipeline
  store.py supa.py                     # Step 6① Supabase writer + client
  run_once.py                          # Step 6② Node-subprocess entry point
  tests/{test_topic_mapping(17), test_runner(9), test_store(9), test_topic_resolve(3)}.py
web/                                   # Step 5/6/7 Next.js frontend
  pages/index.tsx pages/_document.tsx pages/_app.tsx
  pages/api/{run, run/[id], runs, star, starred, topics}.ts
  components/{Sidebar, ReportList, RunTab, StarredTab, TopicPanel, SettingsTab}.tsx
  lib/{types, supabase-server, api, report-mapping}.ts
  styles/globals.css  .env.local (gitignored)
preview/build_preview.py preview.html  # Warm-tone design-preview generator
experiments/{firecrawl,apify}/         # Research spikes
```

---

## 3. The eight steps in detail

### Step 1 — Database schema ✅ Rex approved
- **Files**: `supabase/migrations/0001_init.sql` (276 lines) + `docs/schema.md` (Mermaid ER diagram).
- **9 tables**: `sources` (data-source registry: source_key UK / adapter_class / quota_top20), `topics` (keyword / status active|archived / archived_at), `topics_cache` (topic_keyword UK / subreddits JSONB / cached_at / expires_at / hard_ceiling_at), `operator_lists` (list_type allow|deny / subreddit_name / scope_topic_id FK NULL=global), `runs` (topic_id FK / triggered_by cron|manual / status / ai_mode / sanity_status / config_fingerprint), `posts_archive` (post_id PK / source FK→sources.source_key / source_native_id / tags_json / ai_review JSONB / full_content_url / config_fingerprint / **UNIQUE(source, source_native_id)**), `report_top20` (run_id FK / post_id FK / rank / tier / **UNIQUE(run_id,rank) + UNIQUE(run_id,post_id)**), `starred` (person / post_id FK / deleted_at soft delete / **partial UNIQUE(person,post_id) WHERE deleted_at IS NULL**), `suggested_keywords` (tag_layer / tag_value / occurrence_count / **UNIQUE(tag_layer,tag_value)**).
- **Constraints/indexes**: `uq_topics_one_active` (partial UNIQUE ON topics(status) WHERE status='active' → at most 1 active at any time), GIN index on `posts_archive.tags_json`, `updated_at` trigger (plpgsql `trigger_set_updated_at` attached to 8 tables).
- **5 must-reserves** (Cindy + Richard): (source, source_native_id) UNIQUE + post_id PK / config_fingerprint always carried / all TIMESTAMPTZ (UTC) / soft delete via deleted_at / full_content stored in Storage.
- **Rex-found bugs (fixed)**: ① `report_top20` missing `UNIQUE(run_id, post_id)` (same post could occupy multiple ranks); ② `topics` missing status↔archived_at consistency CHECK; ③ `rank` tightened to `CHECK (rank BETWEEN 1 AND 20)`; ④ (self-audit via pglast) `suggested_keywords.reviewed_decision CHECK ... IN ('add','reject',NULL)` — the NULL let invalid values through → dropped the NULL.
- **Verification**: pglast (libpg_query) parses all 35 statements cleanly.
- **Not done yet**: actually applying to real Supabase (Step 8 deployment).

### Step 2 — Data-source plugin layer ✅ Rex approved
- **Files**: `pipeline/schema.py` + `pipeline/sources/`.
- **HotItem dataclass** (`schema.py`): id / dedup_key / title / source / **source_native_id** (aligned with posts_archive UNIQUE) / url / published_at / captured_at / raw_metrics{likes,comments,saves,upvotes} / source_native / hot_score / relevance_score / tags / raw_snippet. Helpers: `make_id(source, native_id)` (sha1), `canonical_url` (strips utm_/fbclid/ref…), `clip_snippet` (≤500).
- **Pluggable architecture**: `Source` ABC (`base.py`, `fetch()->List[HotItem]`) + `registry.py` (`SOURCE_REGISTRY: source_key→adapter class` + `get_source()` / `build_sources()`). Adding a new source = write the adapter + register in registry + INSERT into the `sources` table; the pipeline itself doesn't change.
- **Adapters**: `RedditSource` (public anonymous .json / oauth client_credentials, two modes; UA validation; exponential backoff + Retry-After; failed_subs), `ProductHuntSource` (rss Atom / token GraphQL, two modes), `XiaohongshuSource` (stub, raises NotImplementedError).
- **Rex-found bugs (fixed)**: `product_hunt_source._fetch_token` was treating GraphQL `200 + errors` as an empty result → **silent failure**. Changed to check top-level `errors`/missing `data.posts` and raise → `fetch()` sets failed=True. + adapter skip-guard for items missing native_id/url.
- **Verification**: compileall + 7 smoke tests (registry / factory / stub raises NIE / unknown source raises KeyError / build_sources / HotItem.to_dict).

### Step 3 — 4-step topic-mapping algorithm ✅ Rex approved
- **Files**: `pipeline/topic_mapping.py` + `tests/test_topic_mapping.py` (17 tests).
- **The 4 steps**: ① Candidate generation (`reddit_search_fn` subreddit search + `llm_suggest_fn` recommendations/synonyms — both injectable; LLM failure fail-soft degrades) ② Scoring `score = 0.65*relevance + 0.35*quality` (relevance from search position + keyword overlap; quality from log-normalized subscriber count, unknown = 0.5) ③ Operator gate: allow/deny (deny wins; allow forces inclusion with score=1.0) + edge-case warnings ④ Cache: 7d TTL (expires_at) / 30d hard_ceiling / `--no-cache`.
- **Cache invariants**: hard_ceiling is set only on first derivation / when ceiling exceeded; **TTL refresh does not push it back** (`_carry_hard_ceiling`).
- **Rex-found bugs (fixed)**: cache originally stored the **post-operator** final result → operator decisions leaked across calls / across topics. Refactored: **cache only stores the pure candidate pool** (`_generate_and_score`); operator is reapplied on every call (`_finalize` / `_finalize_from_cache`). + Landed stale fallback (if derivation fails within hard_ceiling, fall back to stale cache; if ceiling exceeded, fail loud).
- **Empirical**: `default_reddit_search("AI")` live → 403 Blocked (Reddit anon throttled, see §7).

### Step 4 — End-to-end local pipeline ✅ Rex approved
- **Files**: `pipeline/{runner,scoring,merge,config,ai_review}.py` + `tests/test_runner.py` (9 tests).
- **scoring.py** (ported from legacy, M1 FROZEN, don't touch): `hot_score = (w_like*likes + w_comment*comments + w_saveshare*saves) * 0.5^(age_h/half_life)`, normalized to 0–100 per source by max; `relevance` = distinct keywords hit / relevance_full_hit, capped at 1.0; `filter_hot` = three gates.
- **merge.py**: `dedup_items` (merge by dedup_key, keep highest hot, ties broken by source priority), `enrich_tags` (native + `kw:` prefix, ≤max_tags), `select_ranked` (source_quota floor + recency + global hot top-up).
- **config.py**: `DEFAULT_CONFIG` — scoring{1,1,1, half_life 48} / filter{relevance_threshold 0.5, relevance_full_hit 2, hot_top_percent 20, min_absolute_hot_score 2.0} / merge{dedup, dedup_source_priority, source_quota **{product_hunt:2}** (hardening #3)} / output{daily_top_n 20, store_top_n 50} / DEFAULT_KEYWORDS (30 words).
- **runner.py**: `run_pipeline(topic, sources, *, cfg, keywords, review_fn, triggered_by, now) -> RunResult`; `config_fingerprint()` (sha1 of cfg + keyword list, stamped on every source_native); `sanity_check()` (empty / count<10 / ai_degraded / **ai_meta_missing** / source_skew>75% / source_fetch_failed); `build_topic_sources()` (topic mapping → sources, **cfg-driven**). `RunResult`{topic, run_at, status, config_fingerprint, candidates_count, scored_count, posts, top, ai_mode, sanity, failed_sources}.
- **ai_review.py**: `heuristic_review(items, cfg) -> (meta, mode)` placeholder (strong/medium/weak from hot-score percentile); real LLM follows the same interface (injected in Step 6).
- **Rex-found bugs (fixed)**: `build_topic_sources` hardcoded PH `auth_mode='rss'`, ignoring cfg → made cfg-driven. + 🟡 ai_meta_missing sanity guard. + 🟡 all-sources-failed → status=failed.
- **Status**: end-to-end runs on stub data offline; real Reddit/OpenAI/Supabase integration = Step 6.

### Step 5 — Next.js frontend skeleton ✅ Rex approved
- **Files**: `web/` (Pages Router + TS + Tailwind v4).
- **Structure**: `pages/index.tsx` (app shell: `tab` state + `starred: Set<string>` + `toggle(id)`; imports `data/sample-report.json as Report`), `components/` (Sidebar 4 tabs / ReportList tier-grouped + Row + ☆ / RunTab / StarredTab / TopicsTab / SettingsTab), `lib/types.ts` (ReportItem{**id**, rank, title, tier_*, source, likes, comments, url, comment} / Report / tierColor), `styles/globals.css` (warm-tone `@theme` tokens: cream/panel/line/ink/mut/terra/strong/mid/weak).
- **Design** (Anna's call): warm palette + one long list (no card frames) + **all UI copy plain Chinese, no technical jargon**.
- **Rex-found bugs (fixed)**: starred/React key was using `rank` (a sort position, not an identity) → cross-day/topic-switch would mix entries up → switched to stable `id` (URL-derived hash; sample data backfilled with id). + Copy tightened / `as Report` annotated as Step-6 validation point.
- **Verification**: `npm run build` passes; `npm run dev` on :3000 (nohup background, /tmp/system1-web-dev.log). `preview/build_preview.py` generates single-file HTML preview from daily_report.md (screenshot-able with headless Chrome).

### Step 6 — API routes + Supabase ✅ all approved (2026-05-25/26)
- **Supabase live**: Anna 2026-05-25 created the free project (ref ksesknktnwtxexlqtivb), `0001_init.sql` applied via the SQL Editor (9 tables + seed). Two follow-up migrations (also via SQL Editor): **`0002_report_review_fields.sql`** (report_top20 + `comment` + `xhs_title` per-run review columns), **`0003_switch_active_topic.sql`** (plpgsql atomic-switch RPC). Creds in `system1-app/.env` (mode 600, gitignored). Direct DDL from this box isn't reachable (IPv6/pooler) → DDL goes via SQL Editor; data goes via PostgREST (secret key).
- **① Data layer ✅ Rex approved**: `pipeline/store.py` (`runresult_to_rows` pure mapping + `SupabaseStore` with injected client; topic → run → posts_archive → report_top20; starred-library add/remove via soft delete) + `pipeline/supa.py` (get_client from .env) + `tests/test_store.py` (9 tests). **Rex round 1 🔴**: ① posts_archive used a blanket upsert that overwrote history → changed to **append-only** (look up existing → only insert new; `save(res, topic_id=None)` supports an explicit override); ② ensure_topic clashed with `uq_topics_one_active` → conservative path (reuse same-keyword active / another active **fails loud** / only create when no active exists); + 🟡 .env parsing strips quotes; get_starred adds `order(starred_at desc)`.
- **② API + frontend wired to real data ✅ Rex approved**:
  - **Node side**: `web/lib/supabase-server.ts` (server-only lazy build) / `lib/api.ts` method guards / `lib/report-mapping.ts` (DB → ReportItem **runtime boundary validation**, retiring `as Report` 🟡). `pages/api/` = {`run.ts` (POST, Node spawn `python -m pipeline.run_once`, argv not shell, 180s kill, parse last line of stdout as JSON) / `run/[id].ts` (GET, id = latest | num, report_top20 join posts_archive, **latest strictly scoped to active topic**) / `runs.ts` / `star.ts` (POST/DELETE soft delete, 23505 idempotent) / `starred.ts` / `topics.ts` (GET + POST **atomic switch via RPC**)}.
  - **Python side**: `pipeline/run_once.py` (Node-subprocess entry; loads .env → resolve_topic → build_sources → run_pipeline → save; last stdout line is JSON; pipeline logs redirected to stderr) + `pipeline/ai_review.py` adds `openai_review` (gpt-4o-mini via api.openai.com **direct, `requests.Session(trust_env=False)` to bypass the Slock proxy**) + `select_review_fn()` (if OPENAI_API_KEY present → real LLM, else heuristic; **any failure falls back wholesale to heuristic**, deferred item "LLM all-fail → heuristic fallback" landed).
  - **Config**: `web/.env.local` (SUPABASE_URL + SECRET_KEY, gitignored, 600) / `.env` adds `REDDIT_USER_AGENT` (public-anon Reddit now works).
  - **Rex round 2 🔴**: ① comment drift across runs (comment was being read from the append-only posts_archive) → per-run comment / xhs_title now lands in `report_top20` (**migration 0002**; threaded through openai_review meta + runner top + store + mapping + select). ② Frontend reads `r.run_id` from POST /api/run instead of `/latest` (guards against concurrency / cron interleave). + 🟡 ReportItem.id stale comment, loadReport/loadStarred + res.ok check + loadError surface.
- **Real AI live** (Anna 2026-05-25 gave OpenAI key): copied `OPENAI_API_KEY` from `system1-scraper/.env` to `system1-app/.env`, direct connection. Run 11 went live with ai_mode=ai, **20/20 Chinese xhs_title + Chinese critique** landed in report_top20.
- **Deferred closed**: ✅ id → post_id / ✅ `as Report` → boundary validation / ✅ LLM all-fail → heuristic fallback / ✅ public-anon Reddit working (UA fix) / ✅ direct OpenAI. **Remaining (Step 8)**: Vercel NFT warning (run.ts path.resolve); trust_env=False needs re-verification under enterprise proxy / custom-CA deployments.

### Step 7 — Frontend Tabs 1 + 2 complete 🚧 4 items approved / 2 remaining (2026-05-26)
**✅ Approved (4 items)**:
- **Strong-tier-on-top**: ReportList tier sections now sort by **strong → medium → weak priority** (originally "first-seen" order, which under real AI tiers would push strong items below medium). Anna spotted live.
- **Subreddit surfacing**: TopicPanel right column "This batch is sourced from: r/X · r/Y · …" (de-duped from report.items.source, computed in RunTab and passed down).
- **Hardening #2 new/recurring post badge**: posts_archive's run_id (the first-seen run) compared against current run → `is_new`; Row shows "🔁 seen before" badge; RunTab header shows "X new / Y recurring" counts. types/mapping/run-[id] all threaded.
- **Topic management (right column + atomic switch)**: Anna asked to drop the standalone "Topics" tab and move it to RunTab's right side → `TopicPanel.tsx` (new) + RunTab two-column + Sidebar dropped "Topics" + types TabKey narrowed + TopicsTab deleted + main width 3xl → 5xl. Backend atomic switch = `pages/api/topics.ts` POST calls the **`switch_active_topic` plpgsql RPC** (migration 0003; archives old active → reuses/creates target; transaction rollback guarantees exactly-one-active). **Rex 🔴**: the original 3 independent PostgREST calls weren't transactional; a mid-flight failure could leave zero active → moved to RPC. Verified live: switch to brand-new no-run topic → latest null doesn't bleed; switch back reuses; no-op; throughout, exactly one active.

**Step 3 landing layer completion** (2026-05-26, triggered by Anna's "switching topics doesn't change content"):
- Root cause: Step 3's `topic_mapping.py` was Rex-approved as an algorithm, but **was never wired to a real run** — it needed "search Reddit for subreddits", and Reddit's anonymous search has been unstable (gated in §7), so run_once was using hardcoded DEFAULT_SUBREDDITS + DEFAULT_KEYWORDS instead.
- **New `pipeline/topic_resolve.py`**: bypasses Reddit search, uses an LLM (gpt-4o-mini) to recommend subreddits + a separate LLM call for per-topic English relevance keywords; **reuses Step 3 TopicMapper's LLM path** (`reddit_search_fn=_noop`, `llm_suggest_fn=_llm_subreddits`, target = 2× then verification trims down); **`_verify_subreddit` pings `r/X/about.json` for each, dropping 404 hallucinations** (404 = confirmed nonexistent; anything else = keep, let the main fetch try); early-stops at target_count; returns separate **`subreddits_source` / `keywords_source`** (Rex 🔴: must not be merged into one source field, otherwise "LLM subs OK + keywords fell back" gets mis-tagged into the strict gate).
- **`run_once.py`**: `mapping = resolve_topic(args.topic, DEFAULT_SUBREDDITS, cfg["keywords"])` → `build_sources(cfg, mapping["subreddits"])` → `run_pipeline(..., keywords=mapping["keywords"])`. **When `subreddits_source == 'llm'`, `relevance_threshold` is relaxed to 0** (the subreddit itself is the topic filter; relaxing prevents "Taylor Swift"-style titles from being killed because they lack a literal "celebrity" token).
- New `tests/test_topic_resolve.py` (3 tests: no key → both fall back / LLM subs OK + keywords fail still tags subs as llm / partial 404 keeps verified instead of falling back).
- **Rex approved** (2 rounds: round 1 🔴 source merging; fixed → re-review passed). Live celebrity topic: 10 LLM candidates → verification drops 6 hallucinations (r/CelebrityNews etc.) → 5 real subs kept → 31-37 posts / 20 top / ai_mode=ai, **real celebrity content (Sydney Sweeney / Anne Hathaway / Aubrey Plaza)**.

**🚧 Step 7 remaining 2 items**: ① cross-run starred-library filter (person / source); ② MappingResult.notes structured fields (is_stale / warnings[], from Step 3 🟡). **Waiting on Anna to test the 4 landed items + pick what's next.**

### Step 8 — Vercel deployment + cron (needs Anna's GitHub / Supabase / Vercel accounts)
- GitHub repo + Vercel auto-deploy + env secrets + daily cron (09:00 LA) + decision on whether to migrate 8 days of legacy SQLite into Supabase (hardening #5, awaiting Anna).

---

## 4. Production hardening (8 days of operation 5/17–5/24, now incorporated)
Background: daily reports kept shrinking (20 → 18 → 17 → 17 → 15). Root cause = cross-day dedup (reported_ledger) + fixed subreddit list → fewer and fewer fresh posts.
| # | Hardening point | Lands in | Status |
|---|---|---|---|
| 1 | Topic-exhaustion exit (N consecutive days of <X new posts → prompt to switch topic, wires into hard switch) | Step 3/4 → 7 | Todo |
| 2 | Daily report tags "X new / Y recurring" | Step 7 | Todo |
| 3 | PH quota = 2 (source_quota) | Step 4 | ✅ Landed in config |
| 4 | Robustness (retry/backoff, failures not silent, sanity checks, AI heuristic fallback) | Throughout | In progress (parts landed in Steps 2/4) |
| 5 | Whether to migrate 8 days of legacy SQLite to Supabase | Before Step 8 | Awaiting Anna |

## 5. Reddit data strategy (2026, must be decided in Step 6)
- Reddit 2025-11 **Responsible Builder Policy**: shut down self-serve API keys; new apps go through Developer Support manual approval (~7 days, often rejected). REDDIT_CLIENT_ID/SECRET in `.env` are placeholders (real OAuth never used).
- Free access requires an OAuth token; anonymous/unauthenticated requests get rate-limited/blocked at will = intermittent 403s (observed: 06:00 cron works, same endpoint 403s in the afternoon).
- **Three paths (awaiting Anna/Junxi call)**: A official application (slow, suggest Junxi files it) / B anonymous + hardening (current; the adapter retries) / C third-party Apify Reddit actors (trudax-lite $3.40/1k etc; we're ~10.5k/mo ≈ $16–36/mo). **Recommend B primary + evaluate C as fallback + A in parallel.** Plug-in: details hidden behind RedditSource, pipeline doesn't change.

## 6. Deferred / tech debt (as of 2026-05-26)
- ✅ LLM all-fail auto-fallback to heuristic + mode=heuristic (Step 6② `select_review_fn` + `openai_review` exception fallback)
- ✅ Frontend id → real posts_archive.post_id (Step 6① after wiring real DB)
- ✅ `as Report` → boundary validation (Step 6② `lib/report-mapping.ts` runtime check)
- 🚧 MappingResult.notes → structured fields is_stale / warnings[] (Step 7 remaining)
- 🚧 PH token success-path real smoke (needs PH developer token, low priority long-term)
- 🚧 Vercel NFT warning (run.ts `path.resolve(process.cwd(),"..")`, deferred to Step 8 deployment; `trust_env=False` needs re-verification under enterprise proxy / custom-CA deployments)
- 🚧 Reddit OAuth (§7, Anna picked "application is slow" → currently on public-anon + UA fix; Apify fallback to be evaluated)

## 7. Test coverage
- `test_topic_mapping.py` 17 tests (sorting / LLM degradation / allow-deny / cache hit / TTL expiry / hard_ceiling not pushed back / operator doesn't leak across calls / topic scope doesn't leak / stale fallback / `--no-cache` etc.)
- `test_runner.py` 9 tests (end-to-end / PH quota surfaced / failed-source isolation / empty source / fingerprint stability / relevance gate / build_topic_sources cfg / all-sources-failed → status=failed / ai_meta_missing)
- `test_store.py` 9 tests (runresult_to_rows shape / save uses real post_id / tier-name short-name mapping / star soft delete / append-only across runs / insert-only for new / ensure_topic reuses active / fails loud on a different active / per-run comment lands in report_top20)
- `test_topic_resolve.py` 3 tests (no key → fallback / LLM subs OK + keywords fail still tagged llm / partial 404 keeps verified instead of falling back)
- Frontend: `npm run build` + `npm run lint` (TS + compile + static generation + eslint)
- schema: pglast parse (Step 1 self-audit)
- live DB verification: every real Rex-flagged issue gets reproduced live + asserted (append-only drift / per-run comment drift / topic-switch latest doesn't bleed / atomic switch always-one-active, etc.)
- **Review loop**: Steps 1–6 + Step 7 approved items + Step 3 landing-layer completion — at every step Rex found real bugs → fixed → re-reviewed (cumulative: 9 🔴 + dozen-plus 🟡 fixed).
