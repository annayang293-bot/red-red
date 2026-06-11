"""Default pipeline config (aligned with the ratified values from legacy config.yaml).

Once DB/frontend are wired, these become configurable (per topic / keywords / taste); Step 4
just gets the engine running with defaults.
"""
from __future__ import annotations

# Relevance keyword list (more hits = more relevant). Tunable per topic in production.
DEFAULT_KEYWORDS = [
    "ai", "ai agent", "ai app", "ai business", "ai product", "ai startup",
    "ai tool", "arr", "automation", "bootstrapped", "build in public", "claude",
    "customers", "founder", "gpt", "growth", "indie", "indie hacker", "launch",
    "llm", "monetization", "monetize", "mrr", "openai", "pricing",
    "product launch", "revenue", "saas", "side project", "startup",
]

DEFAULT_CONFIG = {
    "scoring": {
        "w_like": 1, "w_comment": 1, "w_saveshare": 1,
        "half_life_hours": 48,            # Time-decay half-life
    },
    "filter": {
        "relevance_threshold": 0.5,       # Relevance gate
        "relevance_full_hit": 2,          # Hitting 2 distinct keywords = max relevance
        "hot_top_percent": 20,            # Relative hotness: must be in Top 20%
        "min_absolute_hot_score": 2.0,    # Absolute floor (Anna ratified 2026-05-21)
    },
    "merge": {
        "dedup": True,
        "dedup_source_priority": ["reddit", "product_hunt"],
        "tag_with_keywords": True,
        "max_tags": 8,
        "tag_prefix": True,
        # Hardening #3: PH quota=2 (zero-engagement sources rely on quota to surface,
        # without crowding out the main leaderboard).
        "source_quota": {"product_hunt": 2},
    },
    "output": {
        "daily_top_n": 20,
        "store_top_n": 50,
    },
    "comments": {
        # Per Top-N item, how many top-scored comments to attach as `source_native.comments`
        # (then surfaced as `comments_summary` in the report + System ② drafting material).
        # Anna 2026-06-11: reverted 30 → 10. The 30 bump (2026-05-31) pushed per-run cost to
        # ~$0.72-0.80 (≈$24/mo @1 run/day), and combined with manual re-runs blew the $29 Apify
        # STARTER cap by 2026-06-08. Back to 10 = ~$0.45/run ≈ ~$13/mo @1 run/day (the original
        # budget); 10 comments is the level that produced 稿件001 S+ and the cognitive-debt draft.
        "max_per_post": 10,
    },
    "reddit": {
        # Listing scrape size per subreddit (drives the Apify listing-run cost — residential proxy
        # GB + compute). Anna 2026-06-11 cost control: 60 → 30. 30/sub × 6 subs = 180 candidates,
        # still ample for a Top-20 (run #71 passed 68 from 360; 180 leaves comfortable headroom).
        "fetch_limit_per_sub": 30,
    },
    "keywords": DEFAULT_KEYWORDS,
}
