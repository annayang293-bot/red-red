# System ② Drafts view — Research review (delta to lil-Dev v1)

> Author: @Richard (research lane).
> Reads against: `SYSTEM2_DRAFTS_VIEW.md` (lil-Dev, commit `871bbd6`).
> Scope: deliberately a delta, not a replacement. Engineering work is solid; surfacing what a
> Research lens catches that an Engineering lens doesn't.

## TL;DR

lil-Dev's design is build-ready engineering. **4 things deserve Anna's eyes before
"approve-to-build", not because they're wrong but because they silently shift PRD §2 or take a
position the doc doesn't flag is debatable.** Plus 4 smaller refinements. Verdict: approve with
the 4 must-flags resolved + 4 improvements folded in; don't approve as-is, don't restart.

## Where I concur (skip if you trust me)

- Inline expansion in `StarredTab.tsx`, lazy fetch on row expand — right call; version history
  is meaningful only relative to its source post; a separate tab forces tab-flipping.
- `BIGSERIAL draft_id` PK, FK to `starred(star_id)`, `trigger_set_updated_at` reuse — correct,
  reuses existing `0001_init.sql` conventions cleanly.
- Hard-delete: none, immutable audit trail with `status='rejected'` for cleanup — right for
  "show iteration history" use case.
- 3 API endpoints (GET/POST/PATCH) under `/api/drafts/*` mirroring the existing star endpoints
  — consistent surface.
- Path A (direct service-role) for the writer agent, given today's threat model (single-tenant,
  single writer, Path X internal-learning) — right at this scale; the migration framing is right.

## 4 must-flag for Anna (decide before build)

### 1. Silent PRD §2 deviations (discipline, not technical)

lil-Dev's design **silently changed two PRD-§2-locked things** without flagging that they
changed:

- **Status enum**: PRD §2 locked **3 states** (`in_progress`/`approved`/`published`); the doc
  proposes **5** (adds `in_review` and `rejected`).
- **Dropped field**: Anna's brief listed `estimated_duration_sec INT` in the field set; the
  doc omits it with no explanation.

Both expansions/cuts may be right calls, but they should be **surfaced for Anna to re-lock or
override**, not silently shipped. Concretely:

- `in_review` is genuinely useful (agent draft → human-review window); recommend keeping but
  flagging as a §2 amendment.
- `rejected` overlaps with "just don't promote" — arguably not needed; one less state to
  maintain. Recommend dropping unless Anna wants explicit abandonment tracking.
- `estimated_duration_sec` is the kind of field System ③ analytics would want
  ("average minutes-per-version landing approval"). Recommend **keep it**; cost is one INT
  column. If dropped, document why so future-Anna doesn't re-litigate.

→ Anna decides: 3 vs 4 vs 5 states; keep or drop duration; lock again.

### 2. Per-person draft chains (architectural clarification, not a bug)

`starred` has a `person` field — Anna / Junxi / Carrie each star independently (different
`star_id` rows for the same `post_id`). Because `drafts.star_id` FKs to `starred`, **drafts are
per-star = per-person**. So if Carrie stars a post Anna already starred, **Carrie gets her own
draft chain**, separate from Anna's.

The doc doesn't say whether this is intended. Two reads:

- **Intended (per-person workspace)**: fine, but the StarredTab UI needs to filter by `person` or
  the version history will mix authors' attempts confusingly.
- **Unintended (drafts should be per-post)**: change FK to `posts_archive(post_id)` instead of
  `starred(star_id)`; only one draft chain per post regardless of who starred.

My read: today only Anna actually writes (System ② Beta is Anna-controlled), so per-person vs
per-post is moot in practice. But the schema choice is sticky — pick deliberately. Recommend
**explicitly state "per-star (= per-person) chains; we'll unify only if multi-editor authorship
emerges"** rather than leave it implicit.

### 3. Schema allows human authorship, recommendation forbids it (contradiction)

`created_by TEXT` schema explicitly contemplates `'anna'` as a value (the doc's own sketch shows
"v2 · anna · approved"). But §5's recommendation says **"let the agent be the only writer, even
when Anna's edits are 'fine, write it like this exactly'"**. These are inconsistent.

Pick one:

- **Allow human authorship** (`created_by='anna'`): Anna is the IP owner; if she wants to write
  v3 herself (because she has a specific opinion), forcing her to dictate to the agent is
  friction. The "always agent" rule contradicts the "网站不编辑，改稿走 Slock" PRD rule's spirit
  — both treat Anna's authorship as central, but the agent-only rule routes everything through
  the agent unnecessarily.
- **Agent-only** (`created_by` is always `agent:*`): cleaner analytics ("how many round-trips
  before approval"), but loses the ability to capture Anna's direct authorship as a first-class
  artifact.

Recommend **allow human authorship** (keep the schema, drop the §5 recommendation). The agent
is the *default* writer; Anna can override. If/when System ③ analytics need to filter, it does
`WHERE created_by LIKE 'agent:%'` — cheap.

### 4. `prev_version_id` self-FK: YAGNI under the locked PRD

The doc adds a self-FK column to encode "branching" (writer tries two angles in parallel).
Cost: one extra column + the conceptual overhead of explaining it. Benefit: hypothetical — PRD
§2 explicitly locked **"网站不编辑，改稿走 Slock"**, and Slock-driven iteration is intrinsically
linear (Anna says "tighten this" → agent writes v2; no parallel branches). The branching case
isn't in any current requirement.

Recommend **drop `prev_version_id`; ordering is `(star_id, version ASC)`**. If a real
branching need ever emerges, add a `branch_id` column then (NULL for the linear majority). The
"survives version-renumbering edge cases" benefit lil-Dev cites is also hypothetical — versions
in an immutable audit trail don't get renumbered.

Net: ship simpler now, defer column until needed. Same simplification stance as PRD §5.1.1
"3 micro-hardenings, no over-engineering."

## 4 worth-noting improvements (fold in, no Anna decision needed)

- **`POST /api/drafts` semantics**: doc says "Insert / upsert" — pick one. Recommend **strict
  INSERT, return 409 on `(star_id, version)` conflict**, writer increments version. Clearer
  audit, fewer accidents than upsert overwriting body. The unique index naturally enforces.
- **Web UI sync model — missing trade-off**: how does the web know a new draft landed? Three
  options: (a) Anna refreshes manually, (b) UI polls `/api/drafts/star/[id]` every 30s when
  expanded, (c) Supabase Realtime push. **Recommend (a) for v1** (no JS state machinery, Anna's
  workflow already has a Slock-to-web context switch), revisit (b) if Anna says "I want it to
  update without me reloading". (c) is overkill at this scale.
- **Path A→B migration "mechanical" claim**: only mechanical if the writer agent's persistence
  is **a single module** (e.g., `writer/persist.py` with `save_draft(...)` that internally
  uses supabase-client). If it's `await supabase.from('drafts').insert(...)` sprinkled across
  agent code, the swap is a rewrite. Recommend: even on Path A today, **wrap the supabase
  client behind one persistence interface** so Path B's later swap is genuinely one-module.
- **RLS roadmap is likely too permissive**: lil-Dev proposes anon `SELECT` on drafts joined to
  non-deleted stars. Drafts are internal work-in-progress (may contain frank notes about other
  people's content, raw ideas, etc.). Recommend **anon DENY on `drafts` for both read and
  write**; only service-role + future auth'd Anna/writer principals get SELECT. Internal-only
  tool today, public read of drafts is a "future regret" risk.

## What I'd cut from the doc (not for Anna, just trim)

- Section 5's "Open question for Anna: does Anna ever author a version directly?" — duplicates
  must-flag #3 above; resolve in the doc rather than ask Anna twice.
- Section 6's `prev_version_id` trade-off — moot if must-flag #4 lands.
- "Deliberately doesn't decide" section is good but currently 4 items; can trim to 2 (the
  body-schema and writer-runtime questions are genuinely deferred; the conflict-policy and
  publishing-telemetry items can be inline).

## Recommended path

Anna sees this delta side-by-side with `SYSTEM2_DRAFTS_VIEW.md`. She rules on the 4 must-flags
(maybe 60 seconds — they're concrete forks). lil-Dev folds in the 4 improvements (≈ 30 minutes
of doc edits, no design rework). Then approve-to-build.

Net design after deltas: same shape as lil-Dev's, **simpler in three places** (no
`prev_version_id`, fewer status states unless Anna wants them, single persistence-interface
module on Path A), **honest in two places** (PRD §2 amendments explicit, sync-model trade-off
named), **stricter in one place** (RLS anon DENY by default on drafts).

---

## Process side note (for lil-Anna, not the doc)

The git Co-Authored-By trailer marks the **model** (Claude Opus 4.7) not the **agent identity**
(Richard / lil-Dev). Combined with shared `user.email` on Anna's laptop, **git history can't
distinguish which agent authored which commit**. This bit me on first read (I thought I'd
written lil-Dev's doc and forgotten). For future parallel-agent work, the most reliable signal
is the commit subject line — the convention "docs: <topic> — <perspective>" (e.g.,
`docs: System ② drafts view — design proposal` vs `docs: System ② drafts view — Research review`)
makes ownership self-evident on `git log`. Worth a one-line note in the contributor convention
if more parallel-agent work is coming.
