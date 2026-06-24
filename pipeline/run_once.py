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
import os
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


def _is_degraded(failed_sources: list, scored_count: int, source_count: int) -> bool:
    """Should this run's exit code be 1 (degraded → workflow red)?

    Pure helper so the policy is easy to unit-test without spinning up Supabase / OpenAI / etc.
    Two signals indicate a degraded run worth alerting on:
      (a) every source instance failed to fetch — total upstream outage.
      (b) the scored set is empty AND at least one source failed — the 3-gate filter had
          nothing to chew on because the only data we got was from a source that couldn't
          produce hot items (run 52 pattern: Reddit blocked + PH-only items can't pass the
          relevance gate, so top is filled only from PH quota and posts == 0).

    A legitimately quiet day (all sources up, just no hot items) leaves failed_sources empty,
    so neither branch fires — keep exit 0 so the workflow stays green.
    """
    all_sources_failed = len(failed_sources) == source_count and source_count > 0
    no_useful_posts = scored_count == 0 and bool(failed_sources)
    return all_sources_failed or no_useful_posts


def build_sources(cfg: dict, subreddits: list[str]) -> list:
    """Build real data sources (Reddit + PH). subreddits is supplied by resolve_topic (LLM recommends per topic;
    falls back to defaults on failure)."""
    reddit_cfg = dict(cfg.get("reddit") or {})
    # Default to apify since 2026-05-31 (Anna): GitHub-hosted runners + Vercel are both
    # datacenter IP class, which Reddit 403s. Apify uses residential proxies and is the only
    # path we've verified works end-to-end. Cost analysis in docs/APIFY_RESEARCH.md.
    # "old_html" / "public" / "oauth" remain in RedditSource as deprecated-but-functional
    # fallbacks; pass reddit.auth_mode in cfg to force.
    reddit_cfg.setdefault("auth_mode", "apify")
    reddit_cfg["subreddits"] = subreddits
    return [
        RedditSource({"reddit": reddit_cfg}),
        ProductHuntSource({"product_hunt": dict(cfg.get("product_hunt") or {})}),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the pipeline once and persist to Supabase")
    ap.add_argument("topic", help="Topic keyword, e.g. 'AI startup'")
    ap.add_argument("--triggered-by", default="manual", choices=["manual", "cron"])
    ap.add_argument(
        "--workspace-id",
        default=None,
        help="BYOK: if set, use this workspace's own (decrypted) Apify token; if omitted, use the "
        "project APIFY_TOKEN (legacy / daily cron path).",
    )
    args = ap.parse_args(argv)

    cfg = DEFAULT_CONFIG
    # Load .env first (Reddit UA / OpenAI key etc. are read by adapters during run_pipeline;
    # get_client also loads it, but that happens after the pipeline runs — too late).
    _load_dotenv_if_present()
    try:
        # pipeline / adapter internals print to stdout → redirect to stderr so that real stdout
        # contains only the final JSON line (so Node can parse it cleanly).
        with contextlib.redirect_stdout(sys.stderr):
            # BYOK (Phase 2): pick this run's Apify token — the workspace's own decrypted token if
            # --workspace-id was given, else the project token already in env. Must run before the
            # pipeline (reddit_source) reads APIFY_TOKEN. Token stays in process memory; never logged.
            from .byok import resolve_apify_token

            _apify = resolve_apify_token(args.workspace_id)
            os.environ["APIFY_TOKEN"] = _apify
            # Log the SOURCE + last 6 only (last6 is non-secret — shown in the UI too). Never log
            # the full token. Lets the Actions log confirm which token path actually ran.
            print(
                "[byok] apify token source="
                + (("workspace " + args.workspace_id) if args.workspace_id else "project-env")
                + f" (…{_apify[-6:]})",
                file=sys.stderr,
            )
            # Share a single supabase client (cache read/write + later save all use it)
            sb_client = get_client()
            # Read the user-supplied mapping_hint (option 3) from the active topic, if any. Hint
            # only matters on cache miss — on hit, the cached subreddits already reflect the prior
            # hint. Empty/missing hint = no extra steering.
            hint = None
            try:
                # Scope the hint lookup to this run's workspace when given (so we don't pick another
                # workspace's same-keyword topic); else fall back to the single global active topic
                # (legacy / daily-cron path). order desc = prefer the newest row if duplicates exist.
                hint_q = sb_client.table("topics").select("mapping_hint").eq("keyword", args.topic)
                if args.workspace_id:
                    hint_q = hint_q.eq("workspace_id", args.workspace_id)
                else:
                    hint_q = hint_q.eq("status", "active")
                trow = hint_q.order("topic_id", desc=True).limit(1).execute().data
                if trow and trow[0].get("mapping_hint"):
                    hint = trow[0]["mapping_hint"]
            except Exception as e:  # noqa: BLE001
                print(f"[run_once] couldn't read mapping_hint, continuing without: {e}", file=sys.stderr)

            # Topic → which subreddits to fetch + per-topic relevance keywords (LLM-derived; each falls
            # back to defaults on failure). Passing cache_client → if topics_cache hits (no TTL, permanent
            # since 2026-05-28), reuse directly so the same topic stays stable across runs.
            mapping = resolve_topic(
                args.topic, DEFAULT_SUBREDDITS, cfg["keywords"],
                cache_client=sb_client,
                hint=hint,
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
        # Wrap the result line — same JSON shape as before. `ok` stays a function of "did we
        # write a row to Supabase", not "did the report have content"; the exit-code policy below
        # is what gates the GH Actions ✅/✗ signal.
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
        # Exit-code policy: degraded run → exit 1 so the GH workflow_run goes red (and Anna
        # sees the problem instead of the run 52 silent-green pattern). See _is_degraded.
        if _is_degraded(res.failed_sources, res.scored_count, len(sources)):
            print(
                f"[run_once] degraded run: failed_sources={res.failed_sources!r}, "
                f"scored_count={res.scored_count}; exiting 1 so the workflow goes red.",
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as e:  # noqa: BLE001 — funnel failures into JSON for Node; don't let stacks spew onto stdout
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
