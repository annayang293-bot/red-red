"""主线默认配置(对齐 legacy config.yaml 的 ratified 值)。

真接 DB/前端后,这些会变成可配置(主题/词表/口味);Step 4 先用默认把引擎跑通。
"""
from __future__ import annotations

# relevance 词表(命中越多越相关)。真产品里按主题可调。
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
        "half_life_hours": 48,            # 时间衰减半衰期
    },
    "filter": {
        "relevance_threshold": 0.5,       # 相关性闸门
        "relevance_full_hit": 2,          # 命中 2 个不同关键词 = relevance 满分
        "hot_top_percent": 20,            # 相对热度:进 Top 20%
        "min_absolute_hot_score": 2.0,    # 绝对地板(Anna 2026-05-21 拍定)
    },
    "merge": {
        "dedup": True,
        "dedup_source_priority": ["reddit", "product_hunt"],
        "tag_with_keywords": True,
        "max_tags": 8,
        "tag_prefix": True,
        # 加固点 #3:PH 配额=2(零互动源靠配额保底露出,不挤占主榜)
        "source_quota": {"product_hunt": 2},
    },
    "output": {
        "daily_top_n": 20,
        "store_top_n": 50,
    },
    "keywords": DEFAULT_KEYWORDS,
}
