"""One-shot run: fetch → score → three gates → dedup → tag → Top-N → AI review → write Supabase.

Invoked by Node `POST /api/run` as a subprocess (single run 30–60s, fits in Vercel Fluid 300s).

Usage: python -m pipeline.run_once "AI startup" [--triggered-by manual|cron]
Contract: **stdout prints exactly one line of result JSON** (so Node can parse it); pipeline / adapter
logs all go to stderr.
  Success: {"ok":true,"run_id":N,"topic":...,"status":...,"ai_mode":...,"posts":M,"top":K,
            "failed_sources":[...],"sanity_status":...}
  Failure: {"ok":false,"error":"..."} + exit code 1.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

from .ai_review import select_review_fn
from .config import DEFAULT_CONFIG
from .runner import run_pipeline
from .sources.product_hunt_source import ProductHuntSource
from .sources.reddit_source import RedditSource
from .store import SupabaseStore
from .supa import _load_dotenv_if_present, get_client
from .topic_resolve import resolve_topic

# Default subreddits for AI-startup-style topics — used as fallback when LLM topic mapping fails.
DEFAULT_SUBREDDITS = ["OpenAI", "SaaS", "Entrepreneur", "startups", "artificial", "indiehackers"]


def build_sources(cfg: dict, subreddits: list[str]) -> list:
    """Build real data sources (Reddit + PH). subreddits is supplied by resolve_topic (LLM recommends per topic;
    falls back to defaults on failure)."""
    reddit_cfg = dict(cfg.get("reddit") or {})
    reddit_cfg.setdefault("auth_mode", "public")
    reddit_cfg["subreddits"] = subreddits
    return [
        RedditSource({"reddit": reddit_cfg}),
        ProductHuntSource({"product_hunt": dict(cfg.get("product_hunt") or {})}),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the pipeline once and persist to Supabase")
    ap.add_argument("topic", help="Topic keyword, e.g. 'AI startup'")
    ap.add_argument("--triggered-by", default="manual", choices=["manual", "cron"])
    args = ap.parse_args(argv)

    cfg = DEFAULT_CONFIG
    # Load .env first (Reddit UA / OpenAI key etc. are read by adapters during run_pipeline;
    # get_client also loads it, but that happens after the pipeline runs — too late).
    _load_dotenv_if_present()
    try:
        # pipeline / adapter internals print to stdout → redirect to stderr so that real stdout
        # contains only the final JSON line (so Node can parse it cleanly).
        with contextlib.redirect_stdout(sys.stderr):
            # Share a single supabase client (cache read/write + later save all use it)
            sb_client = get_client()
            # Topic → which subreddits to fetch + per-topic relevance keywords (LLM-derived; each falls
            # back to defaults on failure). Passing cache_client → if topics_cache hits (no TTL, permanent
            # since 2026-05-28), reuse directly so the same topic stays stable across runs.
            mapping = resolve_topic(
                args.topic, DEFAULT_SUBREDDITS, cfg["keywords"],
                cache_client=sb_client,
            )
            print(f"[run_once] topic mapping subs={mapping['subreddits_source']}/"
                  f"kws={mapping['keywords_source']} "
                  f"| subreddits={mapping['subreddits']} "
                  f"| keywords({len(mapping['keywords'])})={mapping['keywords'][:6]}…")
            # **Only the subreddit source** decides whether to relax the gate (Rex 🔴): LLM-chosen
            # subreddits are themselves a topic filter, so the keyword gate is relaxed (otherwise
            # legitimate celebrity posts like "Taylor Swift" would be killed by titles lacking a literal
            # "celebrity" token). Keyword fallback to the default AI list ≠ subreddit fallback — that
            # case should still be relaxed, so we decouple the two source signals.
            run_cfg = cfg
            if mapping["subreddits_source"] == "llm":
                run_cfg = {**cfg, "filter": {**cfg["filter"], "relevance_threshold": 0.0}}
            sources = build_sources(run_cfg, mapping["subreddits"])
            res = run_pipeline(
                args.topic, sources, cfg=run_cfg, keywords=mapping["keywords"],
                review_fn=select_review_fn(), triggered_by=args.triggered_by,
            )
            # Attach the full LLM-chosen subreddit list to res so store writes it into runs.subreddits
            res.subreddits = list(mapping["subreddits"])
            run_id = SupabaseStore(get_client()).save(res)
        out = {
            "ok": True, "run_id": run_id, "topic": res.topic,
            "status": res.status, "ai_mode": res.ai_mode,
            "posts": res.scored_count, "top": res.top_count,
            "failed_sources": res.failed_sources,
            "sanity_status": res.sanity.get("status"),
            "subreddits": mapping["subreddits"],
            "keywords_count": len(mapping["keywords"]),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — funnel failures into JSON for Node; don't let stacks spew onto stdout
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
