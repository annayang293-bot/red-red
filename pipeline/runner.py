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
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import DEFAULT_CONFIG
from .scoring import score_items, filter_hot
from .merge import dedup_items, enrich_tags, select_ranked
from .ai_review import heuristic_review


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
    """Attach top-N comments to each Top-N item whose source supports `fetch_post_comments`.

    Currently only RedditSource (old_html mode) — other adapters return empty / silently skip.
    Comments are stored on `item.source_native["comments"]` (list of dicts), which then flows
    through to `posts_archive.comments_summary` and the frontend ReportItem.

    Adds ~20 HTTP calls (one per Top-20 item) → ~20-40s per run. Each fetch failure is logged
    and skipped — comments are optional enrichment, never a blocker.
    """
    max_comments = int((cfg.get("comments") or {}).get("max_per_post", 10))
    # Build a quick lookup so we don't scan the sources list 20 times.
    by_source = {getattr(s, "name", None): s for s in sources}
    for it in report_items:
        src = by_source.get(it.source)
        if src is None or not hasattr(src, "fetch_post_comments"):
            continue
        permalink = (it.source_native or {}).get("permalink")
        if not permalink:
            continue
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


def sanity_check(report_items, ai_mode, failed_sources, ai_meta_missing=0):
    """Post-run content sanity scan (Anna locked 5 lightweight checks, hardening #4)."""
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

    # ⑤ AI review (strong/medium/weak)
    meta, ai_mode = review_fn(report_items, cfg)
    top = [{"rank": i + 1, "item": it,
            "tier": (meta.get(it.id) or {}).get("tier"),
            "comment": (meta.get(it.id) or {}).get("comment"),
            "xhs_title": (meta.get(it.id) or {}).get("xhs_title")}
           for i, it in enumerate(report_items)]

    # ⑥ Sanity
    ai_meta_missing = sum(1 for r in top if not r["tier"])
    sanity = sanity_check(report_items, ai_mode, failed, ai_meta_missing=ai_meta_missing)

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
