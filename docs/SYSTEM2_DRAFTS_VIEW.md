# System ② Drafts view — design proposal

> Status: proposal for lil-Anna / Anna review. No code yet.
> Scope: extend the existing System ①-only stack (Next.js / Supabase / writer-agent in Slock)
> so the writer team's drafts persist as versioned rows, and the website surfaces the chain
> per ☆-item.

---

## 1. `drafts` table — migration `0008_drafts.sql`

Field list locked by PRD §2 (`star_id` / `version` / `body` / `created_by` / `prev_version_id` /
`status` / `change_notes`); proposal below fills in keys, constraints, indexes, defaults.

```sql
CREATE TABLE drafts (
  draft_id        BIGSERIAL PRIMARY KEY,
  star_id         BIGINT  NOT NULL REFERENCES starred(star_id),
  version         INT     NOT NULL CHECK (version >= 1),
  body            TEXT    NOT NULL,
  created_by      TEXT    NOT NULL,                         -- e.g. 'agent:writer-1' / 'anna'
  prev_version_id BIGINT  REFERENCES drafts(draft_id),      -- NULL on v1
  status          TEXT    NOT NULL DEFAULT 'in_progress'
                    CHECK (status IN ('in_progress','in_review','approved','published','rejected')),
  change_notes    TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one row per (star, version). Re-running v3 means update-in-place, not insert.
CREATE UNIQUE INDEX uq_drafts_star_version ON drafts (star_id, version);

-- The "all versions for this star" query is the hottest read path (per-star history view).
CREATE INDEX idx_drafts_star_id ON drafts (star_id, version DESC);

-- For ops-style filters: "all drafts currently in_review across stars".
CREATE INDEX idx_drafts_status ON drafts (status) WHERE status IN ('in_review','approved');

-- Hook into the existing trigger_set_updated_at function (lives in 0001_init.sql).
CREATE TRIGGER set_updated_at BEFORE UPDATE ON drafts
  FOR EACH ROW EXECUTE FUNCTION trigger_set_updated_at();
```

**Notes on the choices**:

- `version` is a small int picked by the writer (1, 2, 3…). The unique index makes "is there
  already a v2 for this star?" an O(1) check; the writer agent should `INSERT ... ON CONFLICT
  (star_id, version) DO UPDATE` if it wants to overwrite mid-draft, or always pick `(MAX(version)
  WHERE star_id=…)+1` to append.
- `prev_version_id` is a self-FK rather than just relying on `version-1` arithmetic. Reason: if
  a draft branch ever forks (Anna asks the writer to try two different angles), `prev_version_id`
  encodes the actual lineage; arithmetic alone would lose that. Most chains will be linear and
  `prev_version_id = version_of(star_id, version-1).draft_id`.
- `status` enum is small: `in_progress` → `in_review` → `approved` → `published`, plus
  `rejected` for explicit abandonment. UI can collapse all non-terminal states into a single
  "active" badge if it gets noisy.
- No `posted_url` field yet — when System ② actually publishes to XHS we'll need somewhere to
  record the live URL + published_at timestamp. Either column on `drafts` (when status=published)
  or a separate `published_drafts` row. Defer until Anna locks publishing flow.
- No RLS policies. Both reads and writes go through service-role like the rest of the project
  today. See §4 for the RLS roadmap.

---

## 2. API endpoints

Three endpoints under `web/pages/api/drafts/`:

| Method + path                       | Purpose                              | Auth (today)                  |
|-------------------------------------|--------------------------------------|-------------------------------|
| `GET  /api/drafts/star/[star_id]`   | All versions for one star, ASC order | service-role (server-side)    |
| `POST /api/drafts`                  | Insert / upsert a draft version      | service-role (server-side)    |
| `PATCH /api/drafts/[draft_id]/status` | Change a draft's status            | service-role (server-side)    |

**`GET /api/drafts/star/[star_id]`** — returns `{ drafts: [{draft_id, version, body, created_by, prev_version_id, status, change_notes, created_at, updated_at}, …] }` sorted by `version ASC`. Used by the
StarredTab inline expansion to render the version chain. ~5ms query, single index scan.

**`POST /api/drafts`** — body matches the column set:
```json
{ "star_id": 42, "version": 2, "body": "…", "created_by": "agent:writer-1",
  "prev_version_id": 17, "change_notes": "tightened the hook line" }
```
Server validates `version >= 1`, `created_by` non-empty, and that `star_id` points at an active
(non-soft-deleted) star. Returns the inserted row with its `draft_id`.

**`PATCH /api/drafts/[draft_id]/status`** — body `{ "status": "approved" }`. Server validates the
new status is in the enum AND that the transition is allowed (e.g. you can't jump
`in_progress → published`; force the intermediate `approved`). Same response shape.

Three-endpoint surface mirrors the existing `/api/star` + `/api/starred` split (lifecycle vs
read), so the URL conventions feel familiar.

---

## 3. Frontend — where the view lives

**Recommendation: inline expansion in `StarredTab.tsx`, not a separate top-level tab.**

The writer's job is "given this starred post, draft a version of it." The version chain is only
meaningful relative to its source post — separating them into different tabs makes the writer
flip back and forth. Co-locating them lets Anna read the original post and the v1/v2/v3 thread
in the same view.

Sketch:

```
☆ Starred  (tab)
└─ Row: "AI 创业用 Cursor 一周写完 MVP — by /u/foo · 124↑ · 38 comments"   [⌄]
        └ when expanded:
          ┌─ Original (collapsed snippet of the post body)
          ├─ Drafts:
          │   v1 · agent:writer-1 · 2026-06-02 10:14 · status: in_review
          │       [body preview, click to expand]
          │   v2 · anna           · 2026-06-02 10:38 · status: approved
          │       [body preview]   change_notes: "shortened hook"
          │   v3 · agent:writer-1 · 2026-06-02 11:02 · status: in_progress
          │       …
          └─ "Approve current draft" / "Mark published" actions (when status allows)
```

`web/components/StarredTab.tsx` already has a `Row` import from `ReportList`. Add a new
sub-component `DraftHistory` that takes `star_id` and renders the list. Fetch lazily on first
expand (don't N+1 the starred list).

If/when Anna asks for a "ops view across all stars" (e.g. "show me everything in_review"), we
add a second tab — but that's later, when there's a real reason to flatten.

---

## 4. Writer agent ↔ DB path (security)

Two ways the writer agent can persist a new draft. The trade-off is **simplicity vs blast
radius if the writer is compromised**.

| Path  | What the writer holds        | Pro                                            | Con                                            |
|-------|------------------------------|------------------------------------------------|------------------------------------------------|
| A — Direct Supabase | `SUPABASE_SECRET_KEY`        | One pattern with the pipeline; no new infra    | Compromised writer = full DB read/write (any table) |
| B — Via API         | A scoped bearer for `/api/drafts/*` | Server validates, can rate-limit; blast radius = drafts table only | Adds an HTTP hop + an auth scheme we don't have yet |

**Recommendation: start with A (direct Supabase), record the limitation in the runbook, and
revisit when we wire any of: (a) writers running on infrastructure we don't fully control,
(b) more than one writer agent, (c) writer agents that humans can prompt directly.**

The pipeline already uses service-role; adding the writer with the same pattern is zero new
surface for "today's threat model" (single-tenant internal tool, single writer agent on Anna's
controlled compute). When path B becomes worth the cost we wire it as a follow-up — the
API surface in §2 doesn't need to change, only the auth requirement.

**RLS roadmap (separate, deferred):** even on path A we should write RLS policies for the
anon role on `drafts` so a future "let the writer use anon-tier credentials" migration is
mechanical:
- `select` on drafts: anon read of anything joined to a non-soft-deleted star.
- `insert`/`update`: anon DENY (all writes go through service-role until we wire bearer auth).

---

## 5. Trigger flow

End-to-end happy path:

```
Anna in System ① ──☆──> starred row inserted (star_id=42)
                            │
                            ▼ (Slock workflow watches `starred` table inserts)
       writer agent (@码字机)
            ├─ reads starred + posts_archive for context
            ├─ drafts v1
            └─ POST /api/drafts { star_id:42, version:1, body, created_by, status:'in_review' }
                            │
                            ▼ (Slock workflow surfaces a card in #无情的码字机 to the human reviewer)
       Anna reads in Slock, says "tighten the hook"
                            │
                            ▼ writer agent picks up the feedback (Slock thread → prompt)
            ├─ drafts v2
            └─ POST /api/drafts { …, version:2, prev_version_id:<v1_id>, change_notes:"…" }
                            │
                            ▼
       Anna (web UI) clicks "Approve v2"
            └─ PATCH /api/drafts/<v2_id>/status { status:'approved' }
                            │
                            ▼
       Anna posts to XHS, marks published
            └─ PATCH /api/drafts/<v2_id>/status { status:'published' }
```

Open question for Anna: **does Anna ever author a version directly?** I'd say no — let the agent
be the only writer, even when Anna's edits are "fine, write it like this exactly." Keeps
`created_by` semantics clean (always an agent) and lets us measure how many round-trips it
takes the agent to land an approved version (System ③-style intelligence later).

---

## 6. Open trade-offs (each one a one-line summary)

- **Inline expansion vs separate tab** — picked inline; if Anna wants a flat "all in_review"
  view we add it later as a second tab without restructuring data.
- **Direct Supabase vs API for the writer agent** — picked direct for parity with the pipeline;
  switch to scoped bearer when the threat model widens.
- **`prev_version_id` self-FK vs arithmetic** — picked the FK; it costs one extra column but
  encodes branching for free and survives version-renumbering edge cases.
- **Hard delete vs soft delete on `drafts`** — propose no delete at all (versions are immutable
  audit trail); a `status='rejected'` row replaces "deletion." Cheap on storage, faithful to the
  "show the iteration history" requirement.

---

## What this proposal deliberately doesn't decide

- The body schema (markdown? structured JSON for XHS card sections?) — Anna's call after she
  sees a v1 draft.
- Where the writer agent runs (Slock worker? CronJob? Vercel function?). System ②'s own design
  doc handles this.
- Conflict policy when two writers race on v2 — single-agent today, irrelevant. Future: the
  `uq_drafts_star_version` unique index will surface the race as an SQL 23505, and the writer
  picks v3 (or merges).
- Publishing telemetry (impressions, saves, etc.). Out of scope until we actually publish.
