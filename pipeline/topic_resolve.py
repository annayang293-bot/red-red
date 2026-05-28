"""Topic → reddit subreddits + relevance keywords via LLM. **Closes the Step 3 algorithm → real-fetch wiring.**

Step 3's TopicMapper algorithm itself is Rex-approved; it depended on "search Reddit for subreddits"
which is gated in §7. Here we **bypass Reddit search** and use the LLM (gpt-4o-mini) for both
subreddit recommendations + per-topic keywords, then wire into run_once.
On failure / missing key → each side falls back to defaults.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .topic_mapping import TopicMapper

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

# Quality floor for LLM-suggested subreddits (Anna 2026-05-28: don't let LLM pick a 2k-sub niche
# over r/OpenAI 2.8M). Sub size correlates with engagement / topical authority. 100k is the
# threshold where AI/startup subs split into "big and well-known" vs "small niche".
MIN_SUBSCRIBERS = 100_000
# Below this many verified, high-quality subs, surface a loud warning (the topic mapping is
# probably incomplete and the run will produce thin / lopsided results).
MIN_QUALITY_COUNT = 4


def _openai_json(prompt: str) -> Optional[dict]:
    """Call the LLM and parse JSON. Missing key / any failure → None (caller falls back).
    Direct to api.openai.com, bypassing environment proxies."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import requests
        sess = requests.Session()
        sess.trust_env = False
        r = sess.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] LLM call failed: {e}", file=sys.stderr)
        return None


# Internal: cache the LLM's classification of the most recent _llm_subreddits call so resolve_topic
# can log it once. Single-threaded pipeline; no concurrent-call concern.
_last_intent: dict[str, str] = {}


def _llm_subreddits(keyword: str) -> list[str]:
    """LLM suggests subreddits for the topic (used by TopicMapper's llm_suggest_fn). Ask for 10 → verification trims down.

    Anna 2026-05-28 prompt v2 = two improvements stacked:
    - **Forced classification** (option 2): the LLM must first commit to a content-type category
      (A创业 / B技术 / C工具 / D新闻 / E创作 / F教育) before it lists subreddits. This blocks the
      "AI 创业 → MachineLearning" failure mode, where the LLM defaulted to AI-tech subs because it
      treated the modifier 'AI' as the head.
    - **Few-shot positive+negative examples** (option 1): explicit "AI 创业 → ✅ SaaS / ❌
      MachineLearning" pairs, so the LLM sees the head-final pattern (in Chinese, "X Y" the head is Y).
    """
    # prompt v3 (Anna 2026-05-28): v2 forced single-category and killed cross-category strong subs
    # (e.g. r/OpenAI didn't make it into "AI 创业" because that's category C while the topic
    # classified as A). Fix: primary + secondary classification, with an "obvious mega-sub
    # override" so any well-known >1M-sub community for either the head or modifier is included
    # regardless of category.
    prompt = (
        f"你在帮一个小红书博主从英文 subreddit 找选题灵感。主题: '{keyword}'\n\n"
        "请按步骤思考:\n\n"
        "【第 1 步 · 主形态】这个主题的「核心内容形态」(从下列选一个):\n"
        "  A. 创业 / 商业 / SaaS / 独立开发 / 副业 / 增长\n"
        "  B. 技术学习 / 论文 / 模型训练 / 算法\n"
        "  C. 工具使用 / 产品评测 / 上手教程\n"
        "  D. 文化新闻 / 行业八卦 / 趋势讨论\n"
        "  E. 创作 / 写作 / 设计 / 内容\n"
        "  F. 教育 / 职业 / 学习路径\n\n"
        "⚠️ **复合主题 X Y(中文「修饰+中心」结构)的核心通常在后半截(目标活动),前半截是修饰词**。\n"
        "  - 「AI 创业」核心=创业 → 主=A(✅ SaaS / Entrepreneur;❌ MachineLearning / DeepLearning)\n"
        "  - 「AI 编程」核心=编程 → 主=C 或 B(✅ programming / learnprogramming)\n"
        "  - 「AI 写作」核心=写作 → 主=E(✅ writing / copywriting)\n"
        "  - 「ChatGPT 副业」核心=副业 → 主=A(✅ Entrepreneur / sidehustle)\n\n"
        "【第 2 步 · 辅形态】判断修饰词(X)所在的领域类别,挑一个(或 'none' 如果是纯主题没修饰词):\n"
        "  例如 「AI 创业」修饰=AI → 辅=C(AI 工具/产品)\n"
        "  例如 「Python 创业」修饰=Python → 辅=C(编程工具)\n\n"
        "【第 3 步 · 推荐 10 个版块】**必须凑够 10 个**,结构:\n"
        "  - **6-7 个主形态版块**(核心活动的版块)\n"
        "  - **3-4 个辅形态版块**(修饰词领域的版块) — 这部分**不要省略**,即使主形态够也要给\n"
        "  - 🌟 **强制覆盖规则**:如果主题或修饰词触及以下任意领域,**对应的旗舰版块必须出现在 10 个里**\n"
        "    - 涉及 AI / LLM / ChatGPT → 必须给 r/OpenAI(280 万订阅)和 r/ArtificialIntelligence(90 万)其中至少一个\n"
        "    - 涉及 Python → 必须给 r/Python\n"
        "    - 涉及 JavaScript / TypeScript → 必须给 r/javascript\n"
        "    - 涉及游戏 → 必须给 r/gaming 或 r/gamedev\n"
        "    - 涉及创业 → 必须给 r/Entrepreneur 或 r/startups\n"
        "    - 涉及写作 → 必须给 r/writing\n"
        "    - 这条规则**优先级高于第 1 步的纯主形态分类**;旗舰版块即使不在主形态类别里也要包含\n\n"
        "要求:\n"
        "- 不带 r/ 前缀;只用字母/数字/下划线\n"
        "- 不要广义大众版块如 funny / news / pics\n"
        "- 订阅 <10 万的会被验证步骤剔除,浪费名额\n"
        "- 只给真实存在、拼写正确的版块(例如 r/startups 是 plural,r/Startup 单数几乎不存在)\n"
        "- 宁可少给也别瞎编\n\n"
        '只输出 JSON:{"core_intent": "A|B|C|D|E|F|mixed", "core_noun": "...", "modifier_intent": "A|B|C|D|E|F|none", "subreddits": ["name1", ...]}'
    )
    out = _openai_json(prompt) or {}
    # Stash the classification so resolve_topic can surface it in logs/output. v3 also captures
    # the modifier's intent (e.g. 'AI 创业' = A + C) so the operator can see whether the
    # mega-sub-override rule actually triggered.
    intent = (out.get("core_intent") or "").strip().upper()
    mod_intent = (out.get("modifier_intent") or "").strip().upper()
    noun = (out.get("core_noun") or "").strip()
    if intent:
        suffix = f" + modifier={mod_intent}" if mod_intent and mod_intent != "NONE" else ""
        _last_intent[keyword] = (
            f"{intent} (core={noun!r}){suffix}" if noun else f"{intent}{suffix}"
        )
    return [
        s.strip().lstrip("r/").lstrip("/")
        for s in (out.get("subreddits") or [])
        if isinstance(s, str) and s.strip()
    ]


def _verify_subreddit(name: str, *, min_subscribers: int = MIN_SUBSCRIBERS) -> bool:
    """Ping `r/<name>/about.json` to verify existence + quality.

    - 404 = confirmed nonexistent (LLM hallucination) → drop.
    - 200 + banned/private/restricted → drop.
    - 200 + subscribers < min_subscribers → drop (Anna 2026-05-28 quality floor;
      keeps LLM from picking tiny niche subs).
    - Anything else (200 + healthy + ≥ floor, OR 403/5xx/timeout where we couldn't read subscribers)
      → keep (the main fetch will catch true failures).
    """
    try:
        import requests
        ua = os.environ.get("REDDIT_USER_AGENT", "python:system1-app:v0.1 (by /u/system1app)")
        r = requests.get(
            f"https://www.reddit.com/r/{name}/about.json",
            headers={"User-Agent": ua}, timeout=10,
        )
        if r.status_code == 404:
            return False
        if r.status_code == 200:
            data = r.json().get("data", {}) or {}
            if data.get("subreddit_type") in ("banned", "private", "restricted"):
                return False
            # Subscriber-count gate. If the field is missing/None we don't have enough info to
            # discriminate → keep (fail-safe; the main fetch will discover dead subs).
            subs = data.get("subscribers")
            if isinstance(subs, int) and subs < min_subscribers:
                print(
                    f"[topic_resolve] r/{name} subscribers={subs:,} < {min_subscribers:,} → drop (quality floor)",
                    file=sys.stderr,
                )
                return False
        return True
    except Exception:
        return True   # If verification itself fails, don't block — let the main fetch decide if it really fails.


def _noop_reddit_search(_keyword: str, _limit: int) -> list[dict]:
    """Bypass Reddit subreddit search (anonymous is unstable) — return empty; candidates come solely from the LLM."""
    return []


def _cache_get(client, keyword: str) -> Optional[dict]:
    """Read topics_cache: **hit = reuse, no TTL check** (Anna 2026-05-28: don't want "all subreddits change after 7 days";
    once decided, stays stable). expires_at / hard_ceiling_at are still filled at write time per the schema (NOT NULL),
    but reads don't check them — effectively = never expires.
    To force-refresh the mapping → DELETE FROM topics_cache WHERE topic_keyword=... (or add a UI button later)."""
    try:
        rows = client.table("topics_cache").select("*").eq("topic_keyword", keyword).limit(1).execute().data
        if not rows:
            return None
        row = rows[0]
        # Extract subreddit names + keywords
        subs_blob = row.get("subreddits") or {}
        # Tolerate two shapes: JSON list or {"names":[...]} (we write a list, but the schema's JSONB is flexible)
        if isinstance(subs_blob, list):
            subs = [s for s in subs_blob if isinstance(s, str)]
        elif isinstance(subs_blob, dict):
            names = subs_blob.get("names") or []
            subs = [s for s in names if isinstance(s, str)]
        else:
            subs = []
        kws = row.get("keywords") or []
        if isinstance(kws, list):
            kws = [k for k in kws if isinstance(k, str)]
        else:
            kws = []
        if not subs or not kws:
            return None
        return {"subreddits": subs, "keywords": kws}
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] cache read failed, falling back to LLM: {e}", file=sys.stderr)
        return None


def _cache_set(client, keyword: str, subs: list[str], kws: list[str]) -> None:
    """Write topics_cache (UPSERT on topic_keyword, 7d TTL, 30d hard_ceiling). Failure doesn't affect the main flow."""
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        # Schema requires subreddits / cached_at / expires_at / hard_ceiling_at NOT NULL
        row = {
            "topic_keyword": keyword,
            "subreddits": list(subs),
            "keywords": list(kws),
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "hard_ceiling_at": (now + timedelta(days=30)).isoformat(),
        }
        client.table("topics_cache").upsert(row, on_conflict="topic_keyword").execute()
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] cache write failed (does not affect this run's result): {e}", file=sys.stderr)


def resolve_topic(
    keyword: str,
    fallback_subreddits: list[str],
    fallback_keywords: list[str],
    *,
    target_count: int = 6,
    cache_client=None,
) -> dict:
    """Topic → {subreddits: [...], keywords: [...]}.

    cache_client (Supabase client): if passed → first check topics_cache (no TTL check; permanent
      since 2026-05-28). Hit = reuse directly (fixes "same topic chooses different subreddits each run",
      Anna 2026-05-27 → simplified 2026-05-28 to permanent cache).
    subreddits: reuses Step 3 TopicMapper (LLM-suggest path, no-op reddit-search to bypass the §7 dependency).
    keywords: per-topic relevance keywords via a separate LLM call. Either side's failure falls back to defaults.
    """
    # 0) Cache hit (reuse the same topic) → don't call LLM, **stay consistent**
    if cache_client is not None:
        cached = _cache_get(cache_client, keyword)
        if cached:
            print(f"[topic_resolve] cache hit: subs={cached['subreddits']} | keywords({len(cached['keywords'])})", file=sys.stderr)
            return {
                "subreddits": cached["subreddits"],
                "keywords": cached["keywords"],
                # What's in cache was LLM-derived → treat as llm source (the relax/no-relax logic
                # behaves the same as a freshly-computed LLM result).
                "subreddits_source": "llm",
                "keywords_source": "llm",
            }

    # 1) subreddits: LLM suggest → verify existence (drop 404 hallucinations) → take target_count; if nothing left, fall back
    raw_subs: list[str] = []
    try:
        # Have the LLM path over-supply (_llm_subreddits asks for 10); TopicMapper applies its algorithm
        # to 2*target first; verification trims down.
        mapper = TopicMapper(
            reddit_search_fn=_noop_reddit_search,
            llm_suggest_fn=_llm_subreddits,
            target_count=target_count * 2,
        )
        result = mapper.map_topic(keyword)
        raw_subs = list(result.subreddit_names)
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] TopicMapper failed: {e}", file=sys.stderr)

    # Surface the LLM's classification so the operator can sanity-check that the topic was understood
    # correctly (Anna 2026-05-28: "I want to see what core form LLM judged"). If the intent is wrong
    # (e.g. LLM tagged "AI 创业" as B instead of A), the chosen subs will be wrong too — better to
    # see this early than debug subreddit-by-subreddit.
    intent = _last_intent.pop(keyword, None)
    if intent:
        print(f"[topic_resolve] LLM classified '{keyword}' as content type: {intent}", file=sys.stderr)

    verified: list[str] = []
    for s in raw_subs:
        if _verify_subreddit(s):
            verified.append(s)
            if len(verified) >= target_count:
                break
        else:
            print(f"[topic_resolve] dropping hallucinated/invalid/tiny subreddit: r/{s}", file=sys.stderr)
    used_llm_subs = bool(verified)
    if verified:
        # Quality-floor warning (Anna 2026-05-28): when too few quality subs survive verification,
        # the run will produce thin / lopsided content. We do NOT silently fall back to defaults
        # (the defaults are AI-themed; using them for an "actor" topic would mis-tag the run).
        # Instead, surface the gap loudly so the operator manually curates topics_cache.
        if len(verified) < MIN_QUALITY_COUNT:
            print(
                f"[topic_resolve] ⚠️ topic mapping incomplete: only {len(verified)} quality subreddits found "
                f"(< {MIN_QUALITY_COUNT}); content will be thin. Consider manually setting topics_cache.subreddits "
                f"for keyword={keyword!r}.",
                file=sys.stderr,
            )
        subs = verified
    else:
        # Entire LLM batch unusable → fall back to defaults (topic mismatch is unfortunate, but at
        # least we have content rather than an empty run). Note: fallback_subreddits is the caller's
        # responsibility — pass an empty list if you want a hard "no content" failure for an off-domain topic.
        print("[topic_resolve] no usable subreddits after verification, falling back to defaults", file=sys.stderr)
        subs = list(fallback_subreddits)

    # 2) keywords: per-topic English relevance keywords (Reddit is an English site)
    kw_prompt = (
        f"主题:'{keyword}'。请给 15-20 个英文关键词,用于判断 Reddit 帖子是否相关该主题"
        "(词要具体、判别力强;不要 a/the/is 这种泛词;主题相关的实体/动作/概念都可)。\n"
        '只输出 JSON:{"keywords":["..."]}'
    )
    out = _openai_json(kw_prompt) or {}
    kws_raw = [
        k.strip().lower()
        for k in (out.get("keywords") or [])
        if isinstance(k, str) and k.strip()
    ]
    seen, kws = set(), []
    for k in kws_raw:
        if k not in seen:
            seen.add(k)
            kws.append(k)
    used_llm_kws = bool(kws)
    if not kws:
        kws = list(fallback_keywords)

    # **Tag sources separately** (Rex 🔴): whether to relax the gate **depends only on the subreddit
    # source**, decoupled from the keyword source — otherwise "LLM subreddits OK + keywords fell back
    # to the default AI list" would be mis-tagged as fallback, pushing a non-AI topic back to strict
    # gate + AI keywords and killing legitimate posts.
    result = {
        "subreddits": subs,
        "keywords": kws,
        "subreddits_source": "llm" if used_llm_subs else "fallback",
        "keywords_source": "llm" if used_llm_kws else "fallback",
    }
    # Both sides LLM-derived → write to cache (next run on the same topic hits and stays consistent)
    if cache_client is not None and used_llm_subs and used_llm_kws:
        _cache_set(cache_client, keyword, subs, kws)
    return result
