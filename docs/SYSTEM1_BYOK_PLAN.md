# System ① 多用户 / BYOK / Workspace 开发计划 (v1)

> 状态:**Phase 0 已完成上线(2026-06-18)**;Phase 1 起待续。落地按 Phase 推进,每个 Phase 你 review、说"开始 Phase X"我才写代码。
> 拟定日期:2026-06-17。作者:Anna + Claude。

---

## 0. 目标

让**不同的人/团队**都能用这套系统,**各自用自己的 Apify 额度**搜**自己想搜的主题**;同一个团队内部(如 Anna + Junxi 共运营一个小红书号)**看同一份东西**。

核心机制 = **BYOK(Bring Your Own Key)+ Workspace(工作区)隔离**。

---

## 1. 为什么是这个架构(关键约束回顾)

- 真正抓数据的活儿跑在 **GitHub Actions runner**(临时云机器),不是浏览器、也不是 Vercel。原因:Vercel 跑不了 30–90s 长 Python + 它的 IP 被 Reddit 限流。
- **GitHub `workflow_dispatch` 的 input 是明文进日志的** → 绝不能把用户 token 当 input 传。
- 因此 token 必须:**加密存库 → runner 跑任务时按引用取出、内存里解密用、用完即焚**(行业里这叫 server-side 加密存储,见研究 BYOK 报告)。
- 客户端-only("key 永不上服务器")方案对我们不可行,因为执行者是后端 runner,拿不到浏览器里的 key。

---

## 2. 隔离单位 = Workspace(不是个人)

| | 同一 workspace 内(Anna + Junxi) | 不同 workspace 间(陌生人/别的团队) |
|---|---|---|
| 主题 | **共享** | 隔离 |
| 报告 / 历史(系统①) | **共享同一份**(只抓一次,省一份钱) | 隔离 |
| 收藏 / 草稿(系统②) | **共享**(你 star,Junxi 立刻看到) | 隔离 |
| Apify token | **workspace 的 token = owner 的 token** | 各 workspace 各自的 token / 额度 |

**模型规则:**
- **Owner 建 workspace**(= 自己的账号);可**邀请成员**。
- **成员 = 完整协作者**(能 star、能跑主题、能看全部);不是只读。
- **token 属于 workspace(owner 提供)**;成员蹭这个 token,不用自带。成员想用自己的额度 → 自己另开一个 workspace。
- **"自带 token"的真正单位 = workspace 的 owner。**
- **历史跟 workspace/账号走**,owner 换/删 token 不影响已有历史(token 只是"谁付钱跑"的凭证)。

> ⚠️ 待 Anna 复核的假设:"workspace 内所有跑都用 owner 一个 token"。若将来要"成员在你工作区里也能用他自己的 token 跑他的主题",需按主题区分用谁的 token,更复杂 —— 当前**不做**。

---

## 3. 主题解析:**复用现有逻辑,不重写**

- 用户只填**主题文字**(如"AI 创业"、"职场")。
- 由现有的 [`pipeline/topic_resolve.py`](../pipeline/topic_resolve.py) 的 `resolve_topic()` —— **LLM 自动挑高流量 subreddit + 关键词**(Anna 已训练过"必须选用户量大/每天帖子最多的版块")—— **原封不动复用**。
- 界面**只读展示** AI 挑了哪些子版块(透明),**不让用户手改版块**(用户不知道哪个流量大,手改会降质量)。
- 每个主题有个 **`auto_daily` 开关**(要不要每天自动跑)—— 开关在 Phase 3 建好字段,真正生效在 Phase 4。

---

## 4. 数据模型(最终)

```
auth.users                       -- Supabase Auth(邮箱 magic-link 登录)

workspaces
  id PK / owner_user_id / name / created_at

workspace_members
  workspace_id / user_id / role('owner'|'member') / invited_at
  -- 一个用户可属于多个 workspace(自己的 + 被拉进的)

apify_credentials                -- 每个 workspace 一行(owner 的 token)
  workspace_id PK / nonce / ciphertext / auth_tag / key_version
  token_last6 / account_username / validated_at
  -- RLS:只有该 workspace 成员可读;明文永不落库

topics                           -- 每个 workspace 多个主题
  id / workspace_id / topic_text / resolved_subreddits[] / keywords[]
  auto_daily(bool) / created_by(user_id) / created_at

runs   ←← 新增 workspace_id 列    -- 谁的 workspace 跑的;回填 Anna 旧历史
report_top20 / posts_archive     -- 经 run_id 归属到某 workspace

-- 系统②(以后):starred / draft_tasks / draft_* 均加 workspace_id
```

**RLS 总原则:** 一行数据,只有"其 workspace_id ∈ 我的成员资格"的用户能读写。token 行额外只读自己 workspace。

---

## 5. 安全要点

- **加密**:AES-256-GCM。主钥匙 `TOKEN_ENC_KEY`(Vercel env + GitHub secret 各一份),每行独立 nonce,AAD 绑 `workspace_id`(密文不能被挪到别的 workspace)。
- **粘贴即验证**:用 token 调 `GET /v2/users/me`,无效当场拒;只存 `token_last6` + 用户名供 UI 显示。
- **引导 scoped token**:界面教用户创建"只能运行这个 Reddit 爬虫"的受限 token —— 泄露了别人也只能跑这一个爬虫,读不了对方数据/账单。
- **RLS 全覆盖**;token **绝不打日志**;runner 内存里解密、用完即焚。
- **成本锁(6h 护栏)保留,且按 `(workspace_id, topic)` 算**:不管谁用、用谁的额度,都防止"手一抖连点"烧光**自己**的额度。

---

## 6. 现有系统怎么办(不退化保证)

- **现在的项目-token 每天 cron(写死"AI 创业")在 Phase 0–3 期间原样保留、继续自动跑** —— Anna 的自动日报一天都不断。
- **Anna 的旧历史 #1–86 迁移**:Anna 首次登录拿到账号 → 建她的 workspace → **一行 SQL 把这 86 条 run 的 `workspace_id` 盖成 Anna 的 workspace** → RLS 一生效就出现在她名下。不重抓、不丢数据。
- **cron 的最终去向(Phase 4)**:从"跑一条写死的"升级成"**照名单挨个跑**":遍历所有"有有效 token + 有标记 `auto_daily` 主题"的 workspace,给每个 (workspace, 主题) 各派一次、各用各自 token。届时 Anna 也以普通 workspace 身份并入,**写死的项目-token cron 退休**。

---

## 7. 分期计划(先手动后自动,每步带验收点)

### Phase 0 — 身份 + workspace 地基 ✅ 已完成(2026-06-18)
- Supabase Auth 邮箱 magic-link 登录 + 最简登录 UI。
- 建 `workspaces` / `workspace_members`;注册即建个人 workspace(owner)。
- `runs` 加 `workspace_id`;回填 Anna 的旧历史;全表 RLS。
- **结果**:迁移 `0011` 上线;登录端到端跑通;Anna 账号 = `annayang5757@hotmail.com`,自动建了工作区;回填 66 条(#11–86)。
- **遗留**:"另一个账号看不到"这条要等报告读取切到 RLS 才真正生效(见部署前清单)。

### Phase 1 — Token 保险库
- `apify_credentials` 表 + RLS。
- `/api/apify-token`:POST(验证 `GET /users/me` → AES-256-GCM 加密 → 存)/ DELETE。
- 设置页输入框 + 删除按钮 + scoped-token 创建引导文案。
- **验收**:贴有效 token → 加密入库、显示 last6;贴无效 → 当场拒;删除可用;库里只有密文。

### Phase 2 — runner 解密
- [`_run-pipeline.yml`](../.github/workflows/_run-pipeline.yml) 增加 `workspace_id` input;跑 pipeline 前插一步:按 workspace 取密文 → 解密 → 注入 `APIFY_TOKEN`(绝不打日志)。
- **验收**:为某 workspace 手动跑一次,确认花的是**该 workspace 的 token**(看该 token 账号的 Apify 用量在动,而非项目 token)。

### Phase 3 — 多主题手动跑 + 邀请协作(第一版交付线)
- `topics` 表(多主题 / `auto_daily` 字段先建好不生效)。
- "加主题"(填文字 → `resolve_topic` 预览只读子版块)/ "现在跑" / 按 workspace 看报告。
- 6h 锁按 `(workspace, topic)`。
- 邀请成员(完整协作者);成员看到同一份主题/报告/收藏。
- **验收**:Anna 加主题→跑→看报告;邀请 Junxi;Junxi 看到同样的主题/报告/star;6h 内重跑被锁。

> ✅ 到 Phase 3,核心价值已交付:**登录 → 填自己 token → 搜自己主题 → 看自己(工作区)历史 → 拉队友共享。**

### Phase 4(以后)— 自动每天(按 workspace 扇出)
- cron 遍历"有 token + 有 `auto_daily` 主题"的 workspace,逐个派活,各用各 token。
- 额度耗尽优雅兜底(某 workspace 失败不影响别人)。
- 把 Anna 的项目-token cron 并入、退休写死版。
- **验收**:每个 workspace 的自动主题每天用自己 token 跑;某 workspace 额度用尽不波及他人。

---

## 8. 待办 / 暂不做(记下免得忘)
- 成员在他人 workspace 内用**自己的** token 跑(暂不做,当前 = owner 单 token)。
- 系统②(star/draft)的 workspace 化:等系统② 真正上线时一并加 `workspace_id`。
- 多 workspace 切换 UI(用户属于多个工作区时)—— Phase 3 后视需要。
- 部署仍是手动 `vercel --prod`(见记忆 system1-deploy-is-manual);多用户上线前考虑配 git 自动部署。

---

## 8.5 部署前清单(开放给多人前必办)
- **配自定义 SMTP = 用 Resend**(成本 ≈ $0,Junxi 已有 Resend 账号/经验):Supabase 自带发信限 **2 封/小时、仅供测试**;多人登录前必须接自己的发信服务,否则第 3 个人在同一小时就收不到登录邮件。
  - **Resend 是 Supabase 官方集成**:装上后自动建 API key + 自动填好 Supabase 的 SMTP 设置(不用手抄)。也可手动配:host `smtp.resend.com`、port `465`、user `resend`、pass = Resend API key。
  - 免费档 **3,000 封/月、100/天、永久免费**,我们登录邮件量远远用不完 → $0。
  - 需验证一个发信域名(送达率;域名约 $10/年,可选,测试可先用 Resend 测试域名)。注意有 `{{ .MagicLink }}` 模板不渲染的小坑(配时检查邮件模板)。
- **Vercel 环境变量**:加 `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`(Production)。`NEXT_PUBLIC_*` 在 build 时内联,不配的话线上登录页会报 configError。
- **报告读取切到 RLS**:目前读取走 service key(全局),"别的工作区看不到你的"尚未真正生效;开放多人前需把报告读取改成用户 JWT + anon key,让 RLS 真隔离。
- **登录 gate = 整站需登录**:部署后线上必须登录才能看,确认这是你要的再部署。
- **i18n 整套 BYOK UI**(批量做):`LoginScreen`、`SettingsTab`(Apify token 那块)现在是硬编码中文(违反 `useT()` 约定)。给 Anna/Junxi 用没问题,但开放给英文用户前要走 `lib/i18n.ts` 加 zh/en。一次性做,别零散。
- 部署动作本身仍是手动 `vercel --prod`(先 stash 系统②),见 `memory/system1-deploy-is-manual`。

---

## 8.6 最终交付 — 重写 README(收尾,最后做)
当前 README 较粗糙。收尾时要写一份**详细、能照着用 + 能维护**的 README,至少覆盖:
- **怎么用**:输入什么(主题/token)→ 跑什么(手动「开始跑」/ 每日自动)→ 出什么(Top-20 报告在哪看)。
- **架构一句话**:Vercel 网页 + Supabase + GitHub Actions runner + Apify/OpenAI;各干什么。
- **维护 / 充钱**:用了哪些账号 + 各自充什么钱:
  - Apify(账号 `guagua`,STARTER $29/月,抓 Reddit;BYOK 后每人自带 token)
  - OpenAI(`OPENAI_API_KEY`,gpt-4o-mini,选题分档,按量)
  - Supabase(数据库 + Auth)、Vercel(网页托管)、GitHub Actions(跑 pipeline)、Resend(发信,免费档)
- **所有密钥/环境变量清单**:在哪配(`.env` / Vercel env / GitHub secrets)、各是什么用途、怎么轮换。
- **常见运维**:额度查询、cron 漏跑怎么补、token 过期/更换、迁移怎么应用。

---

## 9. 关联
- 成本/护栏背景:`memory/system1-apify-cost.md`
- 部署坑:`memory/system1-deploy-is-manual.md`
- BYOK 行业做法研究:本次会话的研究 agent 报告(server-side 加密存储 + scoped token + runner 按引用取)。
