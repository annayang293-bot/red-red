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
        # (then surfaced as `comments_summary` in the report). Anna 2026-05-31: bumped 10 → 30
        # so System ② has more raw material per item to draft from. Budget at $0.0013/item still
        # well under Anna's $30/mo Apify cap (20 × 30 = 600 comments/run × ~$0.0013 + listing
        # overhead ≈ $0.80/run × 30 ≈ $24/mo).
        "max_per_post": 30,
    },
    "keywords": DEFAULT_KEYWORDS,
}
