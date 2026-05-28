# AI Xiaohongshu IP

A complete system that takes the pain out of "picking topics + drafting + reviewing" for a Xiaohongshu account. Three subsystems:

- **System ① Topic discovery** (this repo, in development / partially shipped)
- **System ② Draft generation** (planned)
- **System ③ Data analysis** (planned)

---

## System ① · Topic discovery (this repo)

Given a topic (e.g. `AI startup`), it:

1. Auto-discovers the relevant subreddits + keywords for that topic;
2. Scrapes the latest hot posts from those sources, scores and ranks them;
3. Uses AI to judge "can this be ported to Xiaohongshu as a topic?", with a Chinese-language critique + suggested title;
4. Produces a Top 20 report you can star item-by-item.

The active topic can be switched any time (each topic has its own history).

### Stack

- **Frontend**: Next.js 16 + React 19 + Tailwind v4 (`web/`)
- **Backend / pipeline**: Python (`pipeline/`)
- **Database**: Supabase (PostgreSQL)
- **AI review**: OpenAI gpt-4o-mini
- **Deployment**: Vercel (Hobby + Fluid Compute)

### Repo layout

```
supabase/migrations/   # Database schema
pipeline/              # Python pipeline (fetch / score / AI review)
web/                   # Next.js frontend (pages + API routes)
scripts/               # One-shot tooling (data migration, etc.)
docs/                  # Documentation
preview/               # Design preview generator
```

### Local development

1. Configure environment variables (at the repo root). Copy `.env.example` to `.env` and fill in your keys:

   ```
   SUPABASE_URL=...
   SUPABASE_SECRET_KEY=...
   OPENAI_API_KEY=...
   REDDIT_USER_AGENT=python:system1-app:v0.1 (by /u/yourname)
   ```

   The web app's `.env.local` (in `web/`) needs `SUPABASE_URL` and `SUPABASE_SECRET_KEY`.

2. Run the frontend:

   ```
   cd web
   npm install
   npm run dev   # defaults to http://localhost:3000
   ```

3. Run the pipeline once (from the CLI, writes to the database):

   ```
   python3 -m pipeline.run_once "AI startup"
   ```

   Or click "Run" on the web "Run" tab.

4. Run the tests:

   ```
   python3 pipeline/tests/test_runner.py
   python3 pipeline/tests/test_store.py
   python3 pipeline/tests/test_topic_mapping.py
   python3 pipeline/tests/test_topic_resolve.py
   ```

### Database

Apply the Supabase migrations in order in the SQL Editor (`supabase/migrations/000X_*.sql`): 9 core tables + two plpgsql RPCs (atomic topic switch / cascade topic delete).
