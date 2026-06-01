"""Pipeline orchestration (Step 4) — wires every block into an end-to-end flow.

Flow (mirrors legacy main.py):
  Fetch (multi-source) → score → three-gate filter → dedup → tag → Top-N selection (with PH quota)
  → AI review (strong/medium/weak) → sanity self-check → RunResult.

Does not write Supabase (= Step 6): RunResult stays in memory (posts ~ posts_archive, top ~ report_top20).
sources and AI are both injectable so the pipeline can run offline against stubs with tests
(real Reddit / OpenAI integration is deferred to Step 6).
"""
from __future__ import annotations

import collections
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import DEFAULT_CONFIG
from .scoring import score_items, filter_hot
from .merge import dedup_items, enrich_tags, select_ranked
from .ai_review import heuristic_review
from . import transcribe


def config_fingerprint(cfg: dict, keywords: list) -> str:
    """Config fingerprint (used by System ③ V2 calibration grouping): stable hash of key cfg sections + keyword list."""
    payload = {
        "scoring": cfg.get("scoring"), "filter": cfg.get("filter"),
        "merge": cfg.get("merge"), "keywords": sorted(keywords),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "cfg_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


@dataclass
class RunResult:
    topic: str
    run_at: str
    triggered_by: str
    status: str                       # completed | failed
    config_fingerprint: str
    candidates_count: int             # Total fetched candidates
    scored_count: int                 # After three-gate filter + dedup (~ new rows added to posts_archive this run)
    posts: list                       # Scored set (HotItem) — → posts_archive
    top: list                         # [{rank, item, tier, comment}] — → report_top20
    ai_mode: str                      # ai | heuristic
    sanity: dict
    failed_sources: list = field(default_factory=list)
    subreddits: list = field(default_factory=list)   # Full list of subreddits actually fetched (incl. those without Top items)

    @property
    def top_count(self) -> int:
        return len(self.top)


def _enrich_top_with_comments(sources, report_items, cfg):
    """Attach top-N comments to each Top-N item whose source supports the comments interface.

    Two interfaces supported, in priority order:
    - `src.fetch_comments_for_urls(urls)` — batch (one network round-trip for the whole Top-N
      slice of that source). Used by the Apify-backed RedditSource where each per-post call has
      a non-trivial start latency, so 20 sequential calls would dominate wall-clock.
    - `src.fetch_post_comments(permalink, op_author, max_comments)` — per-post (one round-trip
      per item). Old-html RedditSource and any future source default to this; rate-limit pacing
      (`comments.rate_limit_sleep`, default 1.5s) prevents 429s when iterating quickly.

    Comments are stored on `item.source_native["comments"]` (list of dicts), which flows through
    to `posts_archive.comments_summary` and the frontend ReportItem.

    Each fetch failure is logged and skipped; comments are optional enrichment, never a blocker.
    """
    comments_cfg = cfg.get("comments") or {}
    max_comments = int(comments_cfg.get("max_per_post", 10))
    rate_limit_sleep = float(comments_cfg.get("rate_limit_sleep", 1.5))
    by_source = {getattr(s, "name", None): s for s in sources}
    # Group pending Top-N items by their source so a batch-capable source can fetch in one call.
    pending_by_src: dict[str, list[tuple]] = {}
    for it in report_items:
        src = by_source.get(it.source)
        if src is None:
            continue
        if not (hasattr(src, "fetch_post_comments") or hasattr(src, "fetch_comments_for_urls")):
            continue
        permalink = (it.source_native or {}).get("permalink")
        if not permalink:
            continue
        pending_by_src.setdefault(it.source, []).append((it, src, permalink))

    for src_name, pending in pending_by_src.items():
        src = pending[0][1]
        # Batch path: one network call for the whole Top-N slice of this source. Each item's
        # canonical URL (HotItem.url) is the reddit.com permalink — pass that, not the path-only
        # permalink form, because Apify needs absolute URLs.
        if hasattr(src, "fetch_comments_for_urls"):
            urls = [it.url for it, _, _ in pending if it.url]
            try:
                by_path = src.fetch_comments_for_urls(urls, max_comments=max_comments)
            except Exception as e:  # noqa: BLE001
                print(f"[runner] batch comments fetch failed for {src_name}: {e}")
                by_path = {}
            for it, _, permalink in pending:
                comments = by_path.get(permalink) or []
                if not comments:
                    continue
                if it.source_native is None:
                    it.source_native = {}
                it.source_native["comments"] = comments
            continue
        # Per-post fallback (old-html etc.): sequential with rate-limit pacing.
        for idx, (it, _, permalink) in enumerate(pending):
            if idx > 0 and rate_limit_sleep > 0:
                time.sleep(rate_limit_sleep)
            try:
                comments = src.fetch_post_comments(
                    permalink, op_author=it.author, max_comments=max_comments,
                )
            except Exception as e:  # noqa: BLE001 — comment fetch is best-effort
                print(f"[runner] comments fetch failed for {permalink}: {e}")
                continue
            if comments:
                if it.source_native is None:
                    it.source_native = {}
                it.source_native["comments"] = comments


def _enrich_top_with_transcripts(report_items, cfg) -> int:
    """Transcribe Top-N Reddit `hosted:video` posts via the `pipeline.transcribe` module.

    Iterates Top-N, picks out posts whose source_native carries a v.redd.it video, calls
    `transcribe.transcribe_reddit_video`, and on success stashes the transcript fields onto
    `source_native` so `pipeline/store.py::_post_row` writes them to
    posts_archive.{transcript, transcript_lang, transcript_cost_usd}.

    Returns the count of posts we expected to transcribe but couldn't (audio fetch failed,
    Whisper rate-limited, OPENAI_API_KEY missing, etc.) — `runner.sanity_check` adds a
    `video_transcribe_failed:N` anomaly so an operator can spot a systemic failure pattern.
    Individual failures do NOT bubble to `failed_sources` (per A' design 2026-06-01); the
    post still ships with transcript NULL.
    """
    transcribe_cfg = (cfg or {}).get("transcribe") or {}
    if transcribe_cfg.get("enabled") is False:
        return 0
    failed = 0
    for it in report_items:
        sn = it.source_native or {}
        if not transcribe.is_reddit_video_post(sn):
            continue
        try:
            result = transcribe.transcribe_reddit_video(sn)
        except Exception as e:  # noqa: BLE001 — transcribe is best-effort, never raise upstream
            print(f"[runner] unexpected transcribe error for {(sn.get('permalink') or it.url)!r}: {e}")
            result = None
        if not result or not result.get("text"):
            failed += 1
            continue
        # Stash on source_native so the post writes through store._post_row + so AI review can
        # use the text as body-equivalent input (videos typically have empty post body).
        it.source_native = {
            **sn,
            "transcript": result["text"],
            "transcript_lang": result.get("language") or None,
            "transcript_cost_usd": result.get("cost_usd"),
        }
    return failed


def sanity_check(report_items, ai_mode, failed_sources, ai_meta_missing=0,
                 video_transcribe_failed=0):
    """Post-run content sanity scan (Anna locked 5 lightweight checks, hardening #4).

    `video_transcribe_failed` (Anna 2026-06-01, A' design): number of Top-N Reddit
    `hosted:video` posts whose audio fetch or Whisper transcription returned None. Surfacing
    this as an anomaly lets us notice a widespread direct-fetch failure pattern (e.g. v.redd.it
    starts blocking the GH-Actions IP class) without bubbling individual post failures into
    `failed_sources` (which would degrade the whole run).
    """
    n = len(report_items)
    anomalies = []
    if n == 0:
        anomalies.append("empty_report(0 items)")
        return {"status": "OK_WITH_ANOMALY", "anomalies": anomalies, "n": 0,
                "ai_mode": ai_mode, "source_dist": {}, "failed_sources": failed_sources}
    if n < 10:
        anomalies.append(f"item_count_low(n={n}<10)")
    if ai_mode != "ai":
        anomalies.append(f"ai_degraded(mode={ai_mode})")
    # AI review missed some items (Step 6 real LLM parse failure can trigger this) → flag, don't let
    # the daily report be half-empty silently.
    if ai_meta_missing:
        anomalies.append(f"ai_meta_missing({ai_meta_missing}/{n})")
    src_counts = collections.Counter(it.source for it in report_items)
    top_src, top_n = src_counts.most_common(1)[0]
    if top_n / n > 0.75:
        anomalies.append(f"source_skew({top_src}={top_n}/{n}>75%)")
    if failed_sources:
        anomalies.append(f"source_fetch_failed({','.join(failed_sources)})")
    if video_transcribe_failed:
        anomalies.append(f"video_transcribe_failed:{video_transcribe_failed}")
    status = "OK_WITH_ANOMALY" if anomalies else "OK"
    return {"status": status, "anomalies": anomalies, "n": n, "ai_mode": ai_mode,
            "source_dist": dict(src_counts), "failed_sources": failed_sources}


def run_pipeline(topic, sources, *, cfg=None, keywords=None,
                 review_fn=heuristic_review, triggered_by="manual", now=None):
    """Run one pipeline pass. sources = already-built list of Source instances (stubs in tests, real adapters in prod)."""
    cfg = cfg or DEFAULT_CONFIG
    keywords = keywords or cfg["keywords"]
    now = now or datetime.now(timezone.utc)
    fp = config_fingerprint(cfg, keywords)

    # ① Fetch (multi-source; single-source failure isn't fatal, gets recorded in failed)
    all_items, failed = [], []
    for src in sources:
        try:
            items = src.fetch()
        except Exception as e:  # noqa: BLE001 — isolate single-source failures; don't tank the run
            failed.append(getattr(src, "name", "unknown"))
            print(f"[runner] source {getattr(src,'name','?')} fetch failed: {e}")
            continue
        # If the adapter exposes failed_subs (Reddit partial-subreddit failure), record that too
        if getattr(src, "failed_subs", None):
            failed.append(f"{src.name}:{','.join(src.failed_subs)}")
        all_items.extend(items)
        for it in items:
            if it.source_native is None:
                it.source_native = {}
            it.source_native["config_fingerprint"] = fp

    # ②–③ Score + three-gate filter
    score_items(all_items, cfg, keywords)
    hot = filter_hot(all_items, cfg)
    hot = dedup_items(hot, cfg)
    enrich_tags(hot, keywords, cfg)
    hot.sort(key=lambda x: x.hot_score, reverse=True)

    # ④ Top-N selection (PH quota + global hot top-up)
    thr = cfg["filter"]["relevance_threshold"]
    quota_srcs = set((cfg.get("merge", {}) or {}).get("source_quota", {}) or {})
    rel_pool = dedup_items(
        [it for it in all_items if it.source in quota_srcs and it.relevance_score >= thr],
        cfg)
    report_items = select_ranked(hot, cfg, cfg["output"]["daily_top_n"], quota_pool=rel_pool)
    enrich_tags(report_items, keywords, cfg)

    # ④.5 Comments enrichment (Anna 2026-05-31): for each Top-N item from a source that supports
    # fetch_post_comments (currently RedditSource in old_html mode), pull top N comments and
    # attach to source_native["comments"]. Runs BEFORE AI review so the LLM can read community
    # responses. Optional / fail-safe — comment fetch errors don't tank the pipeline.
    _enrich_top_with_comments(sources, report_items, cfg)

    # ④.6 Transcript enrichment (Anna 2026-06-01): for each Top-N Reddit `hosted:video` post,
    # fetch the v.redd.it audio MP4 directly + transcribe via OpenAI Whisper. Result lands on
    # source_native["transcript"] + persists to posts_archive.transcript (et al). Like the
    # comments enrichment above, optional / fail-soft — a failed transcription leaves the post
    # in the report with transcript=NULL; the count of failures is reported via the sanity check.
    video_transcribe_failed = _enrich_top_with_transcripts(report_items, cfg)

    # ⑤ AI review (strong/medium/weak)
    meta, ai_mode = review_fn(report_items, cfg)
    top = [{"rank": i + 1, "item": it,
            "tier": (meta.get(it.id) or {}).get("tier"),
            "comment": (meta.get(it.id) or {}).get("comment"),
            "xhs_title": (meta.get(it.id) or {}).get("xhs_title")}
           for i, it in enumerate(report_items)]

    # ⑥ Sanity
    ai_meta_missing = sum(1 for r in top if not r["tier"])
    sanity = sanity_check(report_items, ai_mode, failed, ai_meta_missing=ai_meta_missing,
                          video_transcribe_failed=video_transcribe_failed)

    # Run state: with sources, if every fetch failed and there are zero candidates = upstream is fully down (failed);
    # otherwise completed (incl. "ran successfully but produced nothing").
    # (Step 6 refines run-state semantics once DB/frontend are wired.)
    status = "failed" if (sources and not all_items and failed) else "completed"

    return RunResult(
        topic=topic, run_at=now.isoformat(), triggered_by=triggered_by,
        status=status, config_fingerprint=fp,
        candidates_count=len(all_items), scored_count=len(hot),
        posts=hot, top=top, ai_mode=ai_mode, sanity=sanity, failed_sources=failed,
    )


def build_topic_sources(topic, mapper, cfg=None, *, reddit_cls=None, ph_cls=None,
                        base_reddit_cfg=None):
    """Convenience: topic mapping → subreddit list → build RedditSource (+PH). For production real-fetch wiring.

    (Step 4's stub tests don't take this path; real Reddit/OAuth integration is Step 6.)
    """
    cfg = cfg or DEFAULT_CONFIG
    mapping = mapper.map_topic(topic)
    subreddits = mapping.subreddit_names
    sources = []
    if reddit_cls is not None:
        # cfg-driven + base_reddit_cfg explicit override; subreddits come from the mapping
        rcfg = {**(cfg.get("reddit") or {}), **(base_reddit_cfg or {})}
        rcfg["subreddits"] = subreddits
        sources.append(reddit_cls({"reddit": rcfg}))
    if ph_cls is not None:
        # cfg-driven: don't hardcode rss, otherwise a real config that wants token mode gets silently downgraded to RSS.
        # When cfg doesn't include product_hunt, the PH source defaults to rss internally.
        pcfg = dict(cfg.get("product_hunt") or {})
        sources.append(ph_cls({"product_hunt": pcfg}))
    return sources, mapping
