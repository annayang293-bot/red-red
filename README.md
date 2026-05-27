# AI 小红书 IP

给小红书账号"省心做选题 + 写稿 + 复盘"的一整套系统。三个子系统:

- **系统① 热点选题**(本仓库代码,开发中 / 部分上线)
- **系统② 稿件生成**(规划中)
- **系统③ 数据分析**(规划中)

---

## 系统① · 热点选题(本仓库)

输入一个主题(比如 `AI 创业`),它会:

1. 自动找出该主题下相关的版块 + 关键词;
2. 抓取这些版块最近的热门帖,打分排序;
3. 用 AI 判断"能不能迁移到小红书做选题",给中文点评 + 标题;
4. 出一份 Top 20 报告,可以一条一条收藏。

主题可以随时切换(每个主题各自一份历史)。

### 技术栈

- **前端**:Next.js 16 + React 19 + Tailwind v4(`web/`)
- **后端 / 主线**:Python(`pipeline/`)
- **数据库**:Supabase(PostgreSQL)
- **AI 点评**:OpenAI gpt-4o-mini
- **部署**:Vercel(Hobby + Fluid Compute)

### 仓库结构

```
supabase/migrations/   # 数据库 schema
pipeline/              # Python 主线(抓取 / 打分 / AI 点评)
web/                   # Next.js 前端(网页 + API 路由)
scripts/               # 一次性工具(数据迁移等)
docs/                  # 文档
preview/               # 设计预览生成器
```

### 本地开发

1. 配置环境变量(在仓库根目录)。复制 `.env.example` 到 `.env`,填入你的 keys:

   ```
   SUPABASE_URL=...
   SUPABASE_SECRET_KEY=...
   OPENAI_API_KEY=...
   REDDIT_USER_AGENT=python:system1-app:v0.1 (by /u/yourname)
   ```

   网页端的 `.env.local`(在 `web/` 里)需要 `SUPABASE_URL` 和 `SUPABASE_SECRET_KEY`。

2. 跑前端:

   ```
   cd web
   npm install
   npm run dev   # 默认 http://localhost:3000
   ```

3. 跑一次主线(命令行,会写库):

   ```
   python3 -m pipeline.run_once "AI 创业"
   ```

   或者在网页 `跑一次` 页直接点"开始跑"。

4. 跑测试:

   ```
   python3 pipeline/tests/test_runner.py
   python3 pipeline/tests/test_store.py
   python3 pipeline/tests/test_topic_mapping.py
   python3 pipeline/tests/test_topic_resolve.py
   ```

### 数据库

Supabase migrations 按顺序在 SQL Editor 跑(`supabase/migrations/000X_*.sql`):核心表 9 张 + 两个 plpgsql RPC(主题硬切换 / 删主题级联)。
