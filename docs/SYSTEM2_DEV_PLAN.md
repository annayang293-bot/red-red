# 系统② Dev Plan v0 — 基于 PRD v1.3

| 字段 | 值 |
|---|---|
| 版本 | v0 (lil-Dev 起草，候 lil-Anna 审 → Anna 拍板) |
| 基于 PRD | 系统②_PRD_v1.md(v1.3 — 2026-06-03 Anna 锁) |
| 范围 | PRD §1-§14 全部，v1 scope；v2 候选(RAG)不含 |
| 节奏 | 8 step，每步 push 后等 Anna 一句话 OK 再进下一步 |
| 整体 ETA | **1.5-2 周专心 + Anna review 节奏**(参考系统① dev plan 实际节奏) |

---

## Step 拆分回应

lil-Anna 建议的 8 步我**几乎照单全收**，只调整两处：

| # | lil-Anna 建议 | 我调整后 | 调整原因 |
|---|---|---|---|
| 1 | Supabase migration(6 表 + 1 列) | **保留** | clean foundation，所有后续 step 依赖 |
| 2 | Webhook backend(`/api/star-notify`) | **保留** | PRD §4 工程量已 detail 过 |
| 3 | draft 数据层 | **拆成「Star UX(60s undo + 弹窗)」** 优先做 | undo 机制是端到端阻塞点(R7 详)，先 nail UX 才能定 webhook 触发时机 |
| 4 | 写稿团队 step_log 集成(Slock client 改造) | **改成「Observer agent + /api/draft API」** | 写稿团队 7 个 agent 都是独立 Slock 进程，不属于我 repo；改他们行为需协调成本 + 风险高(R1 详) |
| 5 | Slock 状态卡 edit-in-place | **不归我** — lil-Anna 自己实现 | 这是 hub agent 的行为，不是 webapp code；我 unblock 她的输入(draft_step_log 表 + Realtime channel) |
| 6 | 网站 UI(StarRow + DraftDrawer + Realtime) | **保留** + 拆成 2 step(数据层 → 组件层) | 单步太大，拆成 6a + 6b |
| 7 | 取消机制 | **吸收进 Step 3** | undo 60s 是 Star UX 的一部分；30 天 cleanup 单独 schedule，不占 step |
| 8 | E2E 联调 + Anna 验收 | **保留** | 上线前 final gate |

**净结果**: 8 步变成 8 步(数量没变，内部 reshuffle 让 each step 更 atomic + 更可 reject)：

1. **Schema migration** — `0009_draft_tables.sql`(starred.prompt_hint + 5 张 draft_ 表 + draft_step_log)
2. **Webhook endpoint** — `/api/star-notify`(HMAC + enrichment + Slock API post)
3. **Star UX + 60s undo + prompt_hint 弹窗** — 客户端 60s undo + 弹窗 + cancel/B 流程
4. **Observer agent + /api/draft API 集合** — channel-pattern 监听器写 draft_step_log + 5 个 draft API endpoint
5. **Slock 状态卡** —(交付给 lil-Anna 实现，我只提供 schema + Realtime channel 文档)
6a. **DraftButton + DraftDrawer 骨架** — Star Tab 行加按钮 + 抽屉容器 + Realtime subscription wire
6b. **DraftHeader + ProgressBoard + VersionTimeline** — 抽屉内 3 个子组件 + 内容填充
7. **Sediment 异步 job** — Vercel cron / GH Actions / Supabase pg_cron 三选一(open question)
8. **E2E 联调 + Anna 验收**

---

## Step-by-step 细节

### Step 1 — Schema migration `0009_draft_tables.sql`

**Time**: 2-3h(单文件 SQL，已经在 PRD §12 把 schema 列得很清楚；主要工作是 index / FK / check constraint 完整化）

**输出**:
- `supabase/migrations/0009_draft_tables.sql`
- `docs/schema.md` 更新(加 6 个新表 ER 图节)
- 一份 `psql` 兼容的 dry-run 脚本(Anna 在 Supabase dashboard 跑前先 dry-run 看 EXPLAIN)

**关键 design decision 我先拍**：
- `draft_tasks.current_status` 用 TEXT + CHECK constraint(5 个 emoji-mapped enum)，不用 PG ENUM 类型(避免迁移 enum 类型痛苦)
- `draft_tasks.thread_short_id` 是 TEXT(Slock 8-char short id)，**NOT NULL**(每个 task 必有 thread)
- `draft_versions.title_options` / `cover_quotes` / `comment_prompts` 用 JSONB 数组(不用 TEXT[]，未来加 metadata 不破 schema)
- `draft_step_log` 加复合 index `(task_id, step_name, status, completed_at DESC)`，进度看板查询是 hot path
- `draft_anna_edits.classified_as` 是 TEXT，**NULL allowed**(writer team NLU 后填，可能 NULL)
- 所有表加 `created_at` / `updated_at` + 复用 `trigger_set_updated_at()`(0001 已有)
- `draft_tasks.star_id` ON DELETE CASCADE — 主理人删 star 时 draft 跟着没；但 PRD §9 说"30 天回收站"，**这条要 Anna 确认**(我倾向 ON DELETE SET NULL + cleanup job 30 天 purge，更安全)

**Tests**: 没 SQL 单测；migration apply 后跑现有 Python suite 确认 starred 表加列没破现有 store.py 逻辑(尤其 `_post_row`)。

**风险**: 极低。pure additive。

---

### Step 2 — `/api/star-notify` webhook endpoint

**Time**: 4-6h

**输出**:
- `web/pages/api/star-notify.ts`(~150 行 TS)
- `web/lib/slock-client.ts`(thin wrapper around Slock REST API for posting messages)
- Vercel env vars: `WEBHOOK_HMAC_SECRET`、`SLOCK_API_TOKEN`(lil-Anna identity)
- Supabase Dashboard 配置(Anna 自己 5 min)

**关键 design**：
- HMAC: SHA256 with `WEBHOOK_HMAC_SECRET`，header `X-Webhook-Signature`(Supabase 支持自定义 header)
- Enrichment query: JOIN `posts_archive` ON `starred.post_id`，取 title / url / source / hot_score / comments_summary(top 10)
- Slock post: 发到 `#无情的码字机` (parent channel)，packet 格式 verbatim PRD §4
- 写一个 `draft_tasks` 行: `status='🟡'`, `current_version=0`, `thread_short_id`=刚发消息的 short id
- Idempotent: 同一个 `star_id` 二次触发 webhook → return 200，no-op(防 Supabase webhook retry 翻倍发消息)

**关键 risk(R4 + R5)**:
- HMAC secret 必须只在 Supabase + Vercel env，**永远不入 git / log**(同 APIFY_TOKEN 处理)
- Slock API token 用谁的身份？**我假设 lil-Anna**(她是 hub)，需 Anna 拍这条
- Slock API down → endpoint 仍写 draft_tasks(status='🟡')，但 thread 没创建 → 写稿团队不知 → 主理人看 Star Tab 显示 🟡 但 thread 没有。**fallback**: 重试 3 次(exp backoff)，仍失败 → status='⚫' + DM Anna "Slock 不可达"

**Tests**: mock Supabase fetch + mock Slock API；测 HMAC verify pass / fail、enrichment join、Slock down 错误处理、idempotent path。

---

### Step 3 — Star UX: 60s undo + prompt_hint 弹窗

**Time**: 4-6h

**输出**:
- `web/components/StarRow.tsx` 改造 + 新弹窗组件 `web/components/StarPromptModal.tsx`
- `web/components/StarUndoToast.tsx` — 60s 倒计时 toast
- `web/pages/api/star.ts` 改造 — POST 加 `prompt_hint` 字段

**关键 design — 60s undo 模式选 client-side hold(不是 server-side delay)**:

```
点 ☆ → 弹窗 (default 走 "直接收藏" or 填 prompt_hint → "加方向后收藏")
       ↓
客户端先在 React state 标 "pending_star" (UI 显示行变 ⏱ 加 toast)
       ↓
启动 60s setTimeout
       ↓
60s 内点 toast "撤回" → 清 setTimeout，本地 state 清；DB 没动过，webhook 没触发 = 0 cost ✅
       ↓
60s 过 → setTimeout 触发 → POST /api/star { post_id, prompt_hint }
       ↓
INSERT INTO starred → Supabase webhook 触发 → /api/star-notify → 写稿团队收 packet
```

**为什么 client-side**:
- 简单(零 server-side schedule 基础设施)
- 真正 "0 cost" — 没 DB 写就没 webhook 触发，PRD §9D "完全没发生过" 严格满足
- 唯一弱点: 用户在 60s 内关浏览器/刷新 → 客户端 state 丢 → star 不会发生。**对内部工具可接受**(用户不会 60s 内关浏览器；如真发生，用户没看到 ☆ 已收藏，可重点)
- 不写 Supabase / 不写 localStorage(避免重新加载意外发生 star)

**B 软删 + 30 天回收站**:
- DELETE on ☆ → `UPDATE starred SET deleted_at = NOW() WHERE star_id = X`(soft delete 复用现有逻辑)
- `draft_tasks.status = '⚫'`(由 `/api/draft/cancel` 完成)
- 写稿团队 in-flight 不停(PRD §9B)
- cleanup job(Step 7 范围)每天扫 `deleted_at < NOW() - 30d` 的 starred + 对应 draft_tasks，hard delete

**Tests**: 客户端 jest test：fake timer 测 60s 行为；点 undo 测 setTimeout 清理；API route 测 prompt_hint 透传。

**风险**: 低。

---

### Step 4 — Observer agent + `/api/draft/*` API endpoints

**Time**: 6-8h(主要是 observer agent design + 5 个 API endpoint)

**输出**:
- `pipeline/observer/draft_observer.py`(新模块；监听 #无情的码字机 channel 消息，pattern-match 写稿团队 step 完成信号，写 draft_step_log)
- 5 个 API endpoint:
  - `GET /api/draft/task/[star_id]` — task + 当前 version
  - `POST /api/draft/version` — 写新版本(写稿团队 / observer 调)
  - `POST /api/draft/edit` — 主理人介入记录(主理人在 thread 发消息时由 lil-Anna observer 转发，或直接 client app 调)
  - `POST /api/draft/cancel` — D undo(noop if pre-insert) + B 软删
  - `GET /api/draft/search?q=...` — 文本搜索(用 pg_trgm，见 R3)

**关键 design — observer agent vs 改造写稿团队(R1)**:

PRD §6 + lil-Anna 建议 Step 4 写"Slock client 改造"。这条对**我** repo 不可行：写稿团队的 7 个 agent 是独立的 Slock 进程，每个有自己的 Anna-side codebase / prompt / loop，他们不属于 `system1-app/`。直接改他们行为需要：
- (a) 每个 agent 加 HTTP POST 到 `/api/draft/step-log`(7 agent 同步改 + 测试)
- (b) Anna 维护 7 个 agent 的 prompt 加"完成 step 时 POST"指令

**两个都是 multi-agent 协调成本**。我提议 v1 用 **observer agent**：

```python
# pipeline/observer/draft_observer.py (运行在 system1-app GH-Actions 或 Vercel cron)
# 监听 #无情的码字机 channel + 子 thread,正则匹配:
#   - "Step 5 final = S" / "Step 7 = A" / "PASS" → Alice 完成 review
#   - "Brief #00X" / "fact-check 全 verify" → Fu 完成 brief
#   - "Jasmine v0/v1/v2" → Jasmine 完成版本
#   - "Carrie lens PASS" → il-Carrie 完成 check
# 解析后 INSERT INTO draft_step_log
```

**优点**: writer team 零改动；observer 一处维护；future-proof(他们换 agent 不影响)
**缺点**: pattern-based parsing 脆(写稿团队改措辞 → observer 漏)；只能 detect "完成"，不易 detect "started" / "blocked"
**Mitigation**: observer 只填 step_name/status='done' + completed_at；step started 状态由 webhook(Step 2)初始化为 "started"

**v2 升级路径**: 写稿团队加显式 HTTP POST 到 `/api/draft/step-log`(precise + complete)；observer fallback。

**Search Chinese tokenization(R3)**:
- Supabase 不支持 pg_jieba / pgroonga(都是 extension，Supabase 没装)
- 用 `pg_trgm`(已装 in Supabase) + `gin_trgm_ops` index + ILIKE / similarity()
- 测试: Anna ☆ ~5-20 条，N 小，pg_trgm 性能 OK；如未来 N>200，再考虑外部 search 服务

**Tests**: observer regex 用真实 channel 历史消息回放测；API 用 mocked Supabase 测；search 用 fixture data 测中文 similarity。

**风险**:
- R1(observer 脆): mitigation 通过定期 review observer 错过的事件 + 写稿团队 v2 转 explicit log
- 实施成本: observer 是新基础设施，调试 channel 消息 pattern 需要时间(可能 8-10h 而非 6-8h)

---

### Step 5 — Slock 状态卡(交付给 lil-Anna)

**Time**: 我侧 0；lil-Anna 自己实现

**我交付**:
- `docs/SYSTEM2_REALTIME_INTERFACE.md` — Realtime channel + draft_step_log schema 给 lil-Anna 看，她写 Python(or 类似)agent，监听 Supabase Realtime on `draft_step_log` INSERT，edit `#无情的码字机:<thread_short_id>` root 下面的状态卡消息
- Schema 已在 Step 1 + Step 4 落地

**风险**: 0(我侧)；lil-Anna 侧她自己评估

---

### Step 6a — DraftButton + DraftDrawer 骨架 + Realtime

**Time**: 6-8h

**输出**:
- `web/components/draft/DraftButton.tsx` — 行右侧按钮(5 状态 + ⏱)
- `web/components/draft/DraftDrawer.tsx` — 右侧 slide-in 抽屉容器(30-40% 宽度，CSS transform animate)
- `web/lib/use-draft-task.ts` — hook：fetch `/api/draft/task/[star_id]` + Realtime subscribe `draft_step_log` + `draft_versions` for this task_id
- `web/components/StarTab.tsx` 改造 — 接入按钮列 + 状态 filter

**关键 design — Realtime subscription**:

```ts
// use-draft-task.ts
const channel = supabase.channel(`draft_task_${star_id}`)
  .on('postgres_changes', { event: '*', schema: 'public', table: 'draft_step_log',
                            filter: `task_id=eq.${task_id}` }, payload => setLog(...))
  .on('postgres_changes', { event: '*', schema: 'public', table: 'draft_versions',
                            filter: `task_id=eq.${task_id}` }, payload => setVersions(...))
  .subscribe();
return () => channel.unsubscribe();   // 关抽屉时清
```

PRD §6 typical ~1.5-2s latency 我同意(实测 Supabase Realtime + Vercel 边缘 ~1-3s)。

**Tests**: mock Supabase Realtime client；测 subscribe / unsubscribe / 重连。手动测在 prod 看真实延迟。

**风险**:
- Vercel cold start 可能让首次 Realtime channel 建立慢(5-10s)；mitigation: 抽屉打开后立即 fetch 一次最新状态，然后 subscribe(不依赖 subscribe 即时同步)

---

### Step 6b — DraftHeader + ProgressBoard + VersionTimeline 内容组件

**Time**: 8-10h

**输出**:
- `web/components/draft/DraftHeader.tsx` — 标题预览 + 状态 emoji + 操作按钮(锁版 / 标已发 / 取消)
- `web/components/draft/DraftProgressBoard.tsx` — step log 渲染(✅ done / 🟡 in-progress / ⚪ pending)
- `web/components/draft/DraftVersionTimeline.tsx` — 折叠 v1/v2/v3 时间线 + 当前版本可展开看内容
- `web/lib/draft-types.ts` — TS 类型

**关键 design**:
- 跳转 Slock thread 链接: `slock://channel/#无情的码字机:<thread_short_id>` 或 web fallback(Slock 是否有 web app？需 lil-Anna 答)
- 默认折叠规则: 历史版本默认收起，最新版本默认展开
- 操作按钮:
  - 「锁版 → 🟢」: POST `/api/draft/task/[task_id]` { status: '🟢' }
  - 「标已发 → 🚀」: 同上
  - 「取消 → ⚫」: POST `/api/draft/cancel`(B 软删 + 30 天)

**Tests**: snapshot test + 手动渲染 fixture 数据。

**风险**: 低(纯 UI)。

---

### Step 7 — Sediment 异步 job

**Time**: 4-6h + 1-2h 选 schedule infra

**核心问题 R2 — sediment 算法没在 PRD 锁**:

PRD §5 sediment 规则说"重复 ≥2 次才入"，但没说**怎么判断 "重复"**。 三种 candidate:

| 方案 | 复杂度 | 准 | 成本 |
|---|---|---|---|
| **A. 字面 substring overlap** | 低 | 低(同义不同表达漏) | 0 |
| **B. embedding 相似度(pgvector + OpenAI text-embedding-3-small)** | 中 | 中-高 | <$0.01/sample,几乎免费 |
| **C. LLM judge "这两条编辑是不是相似偏好"** | 高 | 高 | ~$0.05/sample |

**我推荐 B**(embedding):
- pgvector 已在 Supabase 可用(v2 RAG 也用它)
- 算法: 新 anna_edit 进 → embed → 找已有 anna_edits cosine similarity > 0.85 的；如有 ≥1 个，写 `draft_samples` row(原编辑 + 相似编辑 ids array)，flag `auto_sediment_pending_review = true`
- GUA/Cindy 每周 review 这个 pending list(per PRD §5 异步剔错)

**Schedule infra 候 Anna 拍**:
- Vercel cron(已有 vercel.json，加 `crons` 字段) ← 推荐(同 infra)
- GH Actions cron(已有，但放到 system① pipeline 一起跑感觉混)
- Supabase pg_cron(extension 装 + SQL 函数；偏 ops)

**输出**:
- `pipeline/sediment_loop.py`(若选 GH Actions cron)或 `web/pages/api/cron/sediment.ts`(若选 Vercel cron)
- 30-day cleanup job 同一份脚本/endpoint，跑 `DELETE FROM starred WHERE deleted_at < NOW() - INTERVAL '30 days'`(soft → hard)

**Tests**: mock embedding API；测 similarity 阈值 / 0 / 多匹配；测 30 天 cleanup boundary。

**风险 R2**: 算法选错或阈值不准 → sediment 质量差，但 v1 不阻塞主流程(只是 prompt context 多了点不准的 sample)；GUA/Cindy weekly review 兜底。

---

### Step 8 — E2E 联调 + Anna 验收

**Time**: 4-6h + Anna review 节奏

**E2E 测试场景**:
1. Anna 在 Star Tab ☆ 一条新帖 → 弹窗 → 填 prompt_hint → 「加方向后收藏」→ 60s 倒计时 → 60s 过 → DB INSERT → webhook → Slock thread 出 packet → observer 看到 Alice 起 Brief → step_log 更新 → 抽屉 Realtime 同步 ✅
1.b. 同上但 60s 内点 undo → toast 消失，DB 没动 ✅
2. 写稿团队跑完 → Jasmine v0 → Alice S → 抽屉显示 "🟠 等你看" + DM Anna ✅
3. Anna 在 thread 发 "通篇大佬改 Hinton" → observer detect → 写 draft_anna_edits → writer team v2 → step_log 更新 ✅
4. Anna 点抽屉 "锁版" → 🟢 ✅
5. Anna 点 "标已发" → 🚀 ✅
6. 取消 case: 点 ☆ icon 第二次 → cancel API → 60s undo → 60s 过 → B 软删 + 状态 ⚫ ✅
7. Sediment job 跑 → 重复 ≥2 次的编辑写入 draft_samples + pending review ✅
8. 搜索: 关键词搜历史稿子 → pg_trgm 返回相关 task ✅

**Anna 验收清单**: PRD §2 G1-G4 4 条 goals 都打勾。

**ETA-to-ship**: Step 1-8 估时累加 ~40-55h 工程时间。**专心做 1-1.5 周；考虑 Anna review 节奏 + 必要 iteration ≈ 2 周**。

---

## 工程 risk 总表

| R | 描述 | 严重度 | Mitigation |
|---|---|---|---|
| R1 | 写稿团队 7 个 agent 加 step_log 写入需协调 | **HIGH** | 用 observer agent v1，writer team 零改动；v2 升级到 explicit log |
| R2 | Sediment "≥2 次重复" 算法 PRD 没锁 | MEDIUM | 推荐 embedding (pgvector，<$0.01/sample)；GUA/Cindy weekly review 兜底 |
| R3 | Postgres 中文搜索(无 jieba/pgroonga) | MEDIUM | pg_trgm 替代，N<200 性能 OK |
| R4 | Webhook HMAC 实现 | LOW | 标准 SHA256，Vercel env 存 secret |
| R5 | Slock API 用谁的 token 身份 | LOW | 推荐 lil-Anna(她是 hub)；候 Anna 确认 |
| R6 | Supabase Realtime cold start 延迟 | LOW | 抽屉打开先 fetch 一次再 subscribe；不依赖即时同步 |
| R7 | 60s undo 设计选 client-side hold | LOW | 选客户端 setTimeout，避免 server-side schedule 复杂度；PRD §9D "0 cost" 严格满足 |
| R8 | Vercel auto-deploy on push 没接 | LOW | 现有 backlog 项；每次 web/ 改后手动 `vercel --prod` |
| R9 | `draft_tasks.star_id` 外键 cascade 策略 | LOW | 倾向 ON DELETE SET NULL + cleanup 30 天 hard delete；候 Anna 拍 |
| R10 | Hobby Vercel function 10s timeout | LOW | 当前所有 endpoint 都 <2s；Sediment cron 用 Vercel cron(无 10s 限) |

---

## 开放问题(需 Anna 一句话拍才下手)

| Q | 问题 | 我推荐 |
|---|---|---|
| Q1 | Slock API 用谁的 token 发 packet 到 #无情的码字机 | lil-Anna(hub) |
| Q2 | Sediment 算法 A/B/C(字面/embedding/LLM) | B (embedding) |
| Q3 | Sediment schedule 用 Vercel cron / GH Actions / Supabase pg_cron | Vercel cron(同 infra)|
| Q4 | `draft_tasks.star_id` 外键策略 ON DELETE SET NULL(我推荐)还是 CASCADE | SET NULL |
| Q5 | 60s undo 选 client-side hold(我推荐)or server-side delay | client-side(简单 + 真 0 cost) |
| Q6 | observer agent v1(writer team 零改动)还是直接改 writer team 发 step_log | observer agent v1，v2 再升级 |
| Q7 | 8-step 拆分接受我的 reshuffle 还是 lil-Anna 原版 | 我的(7→5 边界更清) |

---

## Changelog

| 日期 | 版本 | 备注 |
|---|---|---|
| 2026-06-03 | v0 | lil-Dev 起草，候 lil-Anna 审 → Anna 拍 7 个 Q + 8-step OK |
