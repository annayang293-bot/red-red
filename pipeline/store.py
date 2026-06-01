"""Step 6 data layer: RunResult → Supabase (runs / posts_archive / report_top20) + starred-library read/write.

Design:
- `runresult_to_rows(res)`: pure mapping (RunResult → row dicts), no IO, unit-testable.
- `SupabaseStore(client)`: wraps the supabase-py client for real DB writes (needs SUPABASE_URL +
  SUPABASE_SERVICE_ROLE_KEY). Client is injected → tests use a fake, no real DB / network / creds required.

Key (closing the deferred): posts_archive upserts on **UNIQUE(source, source_native_id)** to get
back the real `post_id`; report_top20 / starred always reference the real `post_id` (no more
URL-hash / rank as identity).
Real client: `from supabase import create_client; create_client(url, service_role_key)`.
"""
from __future__ import annotations

from typing import Any, Optional

# AI review uses full names ("强迁移" etc.), but report_top20.tier's CHECK constraint is short names
# ('强'/'中'/'弱'). Map to short names at write time (schema is the single source of truth);
# ai_review JSONB has no constraint, so it keeps the full names (more descriptive).
TIER_DB = {"强迁移": "强", "中等迁移": "中", "弱迁移": "弱"}


def _tier_db(tier):
    return TIER_DB.get(tier, tier)


# ---------------- Pure mapping (unit-testable, no IO) ----------------
def _post_row(it, fp: str, ai: Optional[dict]) -> dict:
    """HotItem → posts_archive row (excluding run_id / post_id, those are filled / fetched at write time)."""
    sn = it.source_native or {}
    # Comments enrichment (Anna 2026-05-31): runner.py's _enrich_top_with_comments attaches a list
    # of {id, score, author, body, is_op, replies} dicts to source_native["comments"] for any
    # Top-N item from a source that supports comment fetch (currently RedditSource). We lift it
    # out into the canonical posts_archive.comments_summary JSONB column so System ② can read it
    # without re-parsing source_native.
    comments_summary = sn.get("comments") if sn.get("comments") else None
    # Transcript enrichment (Anna 2026-06-01): runner._enrich_top_with_transcripts attaches the
    # Whisper output to source_native for Top-N v.redd.it video posts. Lift the three fields onto
    # the canonical posts_archive columns (added in 0008_posts_archive_transcript.sql) so System
    # ② / future analytics can read transcripts without parsing source_native JSON.
    transcript = sn.get("transcript") or None
    transcript_lang = sn.get("transcript_lang") or None
    transcript_cost_usd = sn.get("transcript_cost_usd")
    return {
        "source": it.source,
        "source_native_id": it.source_native_id,
        "title": it.title,
        "url": it.url,
        "raw_snippet": it.raw_snippet,
        "raw_metrics": it.raw_metrics,
        "hot_score": it.hot_score,
        "relevance_score": it.relevance_score,
        "tags_json": it.tags,
        "ai_review": ai,                       # {tier, comment} only present for items in top
        "comments_summary": comments_summary,  # [{score, author, body, is_op, replies}, ...] or None
        "transcript": transcript,                       # Whisper text for v.redd.it videos; NULL otherwise
        "transcript_lang": transcript_lang,             # Whisper-detected language, e.g. "english"
        "transcript_cost_usd": transcript_cost_usd,     # Per-video Whisper cost (USD)
        "published_at": it.published_at,
        "fetched_at": it.captured_at,
        "config_fingerprint": sn.get("config_fingerprint", fp),
        "source_native": sn,
        "full_content_url": None,              # Full text in Storage, wired later
    }


def runresult_to_rows(res) -> dict:
    """RunResult → {run, posts, report}. report refers to posts via (source, source_native_id);
    after the DB upsert we swap that for the real post_id (see SupabaseStore.save)."""
    # Per-item AI review from `top`, indexed by (source, native_id)
    ai_by_key = {
        (r["item"].source, r["item"].source_native_id): {
            "tier": r["tier"], "comment": r["comment"]}
        for r in res.top
    }
    run = {
        "topic_keyword": res.topic,
        "triggered_by": res.triggered_by,
        "status": res.status,
        "started_at": res.run_at,
        "finished_at": res.run_at,
        "posts_count": res.scored_count,
        "top20_count": len(res.top),
        "ai_mode": res.ai_mode,
        "sanity_status": res.sanity.get("status"),
        "sanity_anomalies": res.sanity.get("anomalies"),
        "config_fingerprint": res.config_fingerprint,
        "error_message": None,
        # Full subreddit list (Anna 2026-05-27, so the topic mapping is visible in the UI; only write when non-None)
        "subreddits": list(getattr(res, "subreddits", None) or []) or None,
    }
    # Posts to land in posts_archive = scored set ∪ posts in the report.
    # (select_ranked's PH-quota items may not be in the scored set but show up in the report;
    #  report_top20.post_id is a FK, so those posts must also exist in posts_archive — otherwise
    #  the FK breaks / report rows are lost.)
    seen: set = set()
    union = []
    for it in list(res.posts) + [r["item"] for r in res.top]:
        k = (it.source, it.source_native_id)
        if k in seen:
            continue
        seen.add(k)
        union.append(it)
    posts = [
        _post_row(it, res.config_fingerprint,
                  ai_by_key.get((it.source, it.source_native_id)))
        for it in union
    ]
    # Report rows carry per-run review (comment / xhs_title) — these change run to run, so they land
    # in report_top20, NOT in the append-only posts_archive (otherwise a post re-entering the report
    # would read the first run's stale review, Rex 🔴1).
    report = [
        {"rank": r["rank"], "tier": r["tier"],
         "comment": r.get("comment"), "xhs_title": r.get("xhs_title"),
         "source": r["item"].source, "source_native_id": r["item"].source_native_id}
        for r in res.top
    ]
    return {"run": run, "posts": posts, "report": report}


# ---------------- Supabase read/write (client injected) ----------------
class SupabaseStore:
    def __init__(self, client: Any):
        self.c = client

    def _exec(self, q):
        res = q.execute()
        return getattr(res, "data", res)

    def ensure_topic(self, keyword: str) -> int:
        """Resolve the topic_id this run belongs to — aligned with the "at most 1 active topic" model.

        Rules (don't implicitly switch topics / don't reuse archived ones in save):
          1) An active topic matching the keyword exists → use it (normal case).
          2) A different keyword has an active topic already → fail loud (switching topics must
             go through the topic-management layer explicitly).
          3) No active topic at all → create an active one for this keyword (won't collide with uq_topics_one_active).
        """
        match = self._exec(
            self.c.table("topics").select("topic_id")
            .eq("keyword", keyword).eq("status", "active").limit(1))
        if match:
            return match[0]["topic_id"]
        other = self._exec(
            self.c.table("topics").select("topic_id,keyword")
            .eq("status", "active").limit(1))
        if other:
            raise RuntimeError(
                f"Active topic already exists (keyword={other[0].get('keyword')!r}); does not match this run's "
                f"keyword={keyword!r}. Topic switching must go through the topic-management layer, "
                "not be implicitly created/switched inside save(). Pass topic_id=... to save() if you want to override.")
        created = self._exec(self.c.table("topics").insert(
            {"keyword": keyword, "status": "active"}))
        return created[0]["topic_id"]

    def save(self, res, topic_id: Optional[int] = None) -> int:
        """Persist one RunResult: topic → run → posts → report_top20 (with real post_ids). Returns run_id.

        posts_archive is an **append-only historical snapshot** (Rex 🔴1): the same post (source,
        source_native_id) only gets inserted on first sight; later runs that re-encounter it reuse
        the existing post_id and **never overwrite history rows**.
        (run_id / hot_score / relevance_score / ai_review / config_fingerprint are all "snapshot
         of that run"; a blanket upsert would silently rewrite older runs' history → report_top20
         joins back to posts_archive would then show a distorted historical view. So this is rewritten
         as "lookup existing → insert new only".)

        topic_id can be passed explicitly (the upper layer decides on topic-switch scenarios); without it,
        ensure_topic's conservative resolver is used.
        """
        rows = runresult_to_rows(res)
        if topic_id is None:
            topic_id = self.ensure_topic(res.topic)

        run_row = dict(rows["run"], topic_id=topic_id)
        run_id = self._exec(self.c.table("runs").insert(run_row))[0]["run_id"]

        # posts_archive: look up existing rows (by source_native_id), only insert posts never seen before;
        # existing ones reuse their post_id — history rows are written once, never overwritten.
        key_to_pid: dict[tuple, int] = {}
        if rows["posts"]:
            nids = [p["source_native_id"] for p in rows["posts"]]
            existing = self._exec(
                self.c.table("posts_archive")
                .select("post_id,source,source_native_id")
                .in_("source_native_id", nids))
            # UNIQUE is (source, source_native_id), so index by the 2-tuple
            # (in_ may return rows from other sources sharing the same native_id; indexing by the
            #  tuple guarantees we don't misattribute).
            for r in existing:
                key_to_pid[(r["source"], r["source_native_id"])] = r["post_id"]
            new_rows = [
                dict(p, run_id=run_id) for p in rows["posts"]
                if (p["source"], p["source_native_id"]) not in key_to_pid
            ]
            if new_rows:
                # supabase-py insert defaults to return=representation, so it returns full rows with post_id.
                saved = self._exec(self.c.table("posts_archive").insert(new_rows))
                for r in saved:
                    key_to_pid[(r["source"], r["source_native_id"])] = r["post_id"]

            # Enrichment refresh for existing rows (Anna 2026-05-31 / 2026-06-01).
            # posts_archive is append-only for *content* (Rex 🔴1) — title/score/etc. snapshot at first
            # insert. But certain fields are *enrichments* that strictly improve over time when a post
            # re-enters Top-N on a later run, and updating just those specific columns is consistent
            # with Rex's invariant:
            #   - `comments_summary`  : more recent community thread is more useful for System ② drafting
            #   - `transcript` + lang + cost_usd : a video post that re-enters Top-N gets its Whisper
            #     output written; before this loop, an existing video post would have transcript=NULL
            #     forever because only the INSERT path carried the new columns.
            #   - source_native's post-intrinsic keys (`post_type`, `content_url`) : these are facts
            #     about the post itself, not run-snapshot signals. Backfill missing keys on old rows
            #     without overwriting the rest of the JSON (preserves config_fingerprint etc.).
            for p in rows["posts"]:
                key = (p["source"], p["source_native_id"])
                pid = key_to_pid.get(key)
                if pid is None:
                    continue
                # If this row was just inserted, the insert already carried these fields — skip.
                if any((nr.get("source"), nr.get("source_native_id")) == key for nr in new_rows):
                    continue

                # Build a sparse patch: only include columns that have new content.
                patch: dict = {}
                new_comments = p.get("comments_summary")
                if new_comments:
                    patch["comments_summary"] = new_comments
                new_transcript = p.get("transcript")
                if new_transcript:
                    patch["transcript"] = new_transcript
                    if p.get("transcript_lang"):
                        patch["transcript_lang"] = p["transcript_lang"]
                    if p.get("transcript_cost_usd") is not None:
                        patch["transcript_cost_usd"] = p["transcript_cost_usd"]
                # Backfill `post_type` / `content_url` into existing source_native if absent.
                # Only fire if the new HotItem actually has these (i.e. this run came from
                # apify-listing harshmaur output, not an older fetch_mode that didn't capture them).
                sn_new = p.get("source_native") or {}
                backfillable = {k: sn_new[k] for k in ("post_type", "content_url")
                                if sn_new.get(k)}
                source_native_merged = None
                if backfillable:
                    # Read-modify-write keeps unrelated keys (config_fingerprint, link_flair_text,
                    # historical permalink) intact while filling the new post-intrinsic ones.
                    existing = self._exec(
                        self.c.table("posts_archive")
                        .select("source_native").eq("post_id", pid).limit(1))
                    current_sn = (existing[0].get("source_native") if existing else None) or {}
                    merged = dict(current_sn)
                    for k, v in backfillable.items():
                        if not merged.get(k):
                            merged[k] = v
                    if merged != current_sn:
                        source_native_merged = merged
                        patch["source_native"] = merged

                if not patch:
                    continue
                # Failure mode policy aligned with _enrich_top_with_comments / _enrich_top_with_
                # transcripts: enrichment updates are best-effort. A Supabase 5xx here should not
                # collapse the whole run; log and continue.
                try:
                    self._exec(
                        self.c.table("posts_archive")
                        .update(patch).eq("post_id", pid))
                except Exception as e:
                    # Keep the key names in the log (not the values) so we can grep failure
                    # patterns without leaking transcript content.
                    print(f"[store] enrichment update failed for post_id={pid} "
                          f"(keys={sorted(patch.keys())}): {e}")

        # report_top20: reference the real post_id (top posts must be in key_to_pid: either pre-existing or just inserted)
        rep_rows = []
        for r in rows["report"]:
            pid = key_to_pid.get((r["source"], r["source_native_id"]))
            if pid is None:
                continue   # Defensive: the union step above guarantees top posts entered posts_archive.
            rep_rows.append({"run_id": run_id, "post_id": pid,
                             "rank": r["rank"], "tier": _tier_db(r["tier"]),
                             "comment": r.get("comment"), "xhs_title": r.get("xhs_title")})
        if rep_rows:
            self._exec(self.c.table("report_top20").insert(rep_rows))
        return run_id

    # ---- Starred library ----
    def add_star(self, person: str, post_id: int, run_id: Optional[int] = None) -> None:
        self._exec(self.c.table("starred").insert(
            {"person": person, "post_id": post_id, "run_id": run_id}))

    def remove_star(self, person: str, post_id: int) -> None:
        # Soft delete: set deleted_at (works with the partial UNIQUE on active rows)
        from datetime import datetime, timezone
        self._exec(
            self.c.table("starred")
            .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
            .eq("person", person).eq("post_id", post_id).is_("deleted_at", "null"))

    def get_starred(self, person: str) -> list:
        # Sort by starred-time descending (newest first) — the frontend starred library needs a stable order;
        # don't rely on the DB's default row order (Rex 🟡).
        return self._exec(
            self.c.table("starred").select("*, posts_archive(*)")
            .eq("person", person).is_("deleted_at", "null")
            .order("starred_at", desc=True))
