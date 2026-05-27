"""Step 3 主题映射 — 测试(用 stub 注入,不依赖网络/API)。

跑法:python3 system1-app/pipeline/tests/test_topic_mapping.py
(纯 assert,不需要 pytest;函数名 test_* 也可被 pytest 收集)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.topic_mapping import (  # noqa: E402
    TopicMapper, InMemoryCacheStore, resolve_operator_lists, _quality_from_subs,
)

UTC = timezone.utc
T0 = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


def _search_stub(results):
    return lambda kw, limit: list(results)


def test_basic_ranking_and_count():
    search = _search_stub([
        {"name": "OpenAI", "subscribers": 2_000_000},
        {"name": "artificial", "subscribers": 1_000_000},
        {"name": "MachineLearning", "subscribers": 500_000},
        {"name": "tinysub", "subscribers": 50},
    ])
    m = TopicMapper(search, target_count=3)
    res = m.map_topic("AI", now=T0)
    assert not res.from_cache
    assert len(res.subreddits) == 3, res.subreddit_names
    # 高订阅 + 靠前 → 排前面;tinysub 应被截掉
    assert "tinysub" not in res.subreddit_names
    # 分数单调不增
    scores = [c.score for c in res.subreddits]
    assert scores == sorted(scores, reverse=True), scores
    print("✅ test_basic_ranking_and_count")


def test_llm_and_synonym_merge():
    search = _search_stub([{"name": "startups", "subscribers": 1_500_000}])
    llm = lambda kw: ["Entrepreneur", "startups"]  # startups 与 search 重合 → 合并 source
    m = TopicMapper(search, llm_suggest_fn=llm, target_count=5)
    res = m.map_topic("创业", now=T0)
    names = {c.name.lower() for c in res.subreddits}
    assert "entrepreneur" in names and "startups" in names
    startups = next(c for c in res.subreddits if c.name.lower() == "startups")
    assert set(startups.sources) >= {"search", "llm"}, startups.sources
    print("✅ test_llm_and_synonym_merge")


def test_llm_failure_degrades():
    search = _search_stub([{"name": "OpenAI", "subscribers": 2_000_000}])
    def boom(kw):
        raise RuntimeError("llm down")
    m = TopicMapper(search, llm_suggest_fn=boom)
    res = m.map_topic("AI", now=T0)  # 不应抛错,降级到仅搜索候选
    assert res.subreddit_names == ["OpenAI"]
    print("✅ test_llm_failure_degrades")


def test_operator_allow_deny():
    search = _search_stub([
        {"name": "OpenAI", "subscribers": 2_000_000},
        {"name": "memes", "subscribers": 5_000_000},   # 高质量但要被 deny
    ])
    m = TopicMapper(search, target_count=5)
    res = m.map_topic(
        "AI", now=T0,
        allow_list={"LocalLLaMA"},   # 不在候选里 → 强制纳入
        deny_list={"memes"},         # 永久剔除
    )
    names = {c.name.lower() for c in res.subreddits}
    assert "memes" not in names, "deny 没生效"
    assert "localllama" in names, "allow 没强制纳入"
    forced = next(c for c in res.subreddits if c.name.lower() == "localllama")
    assert forced.forced and forced.score == 1.0
    assert "memes" in [n.lower() for n in res.deny_list_applied]
    print("✅ test_operator_allow_deny")


def test_deny_overrides_allow():
    search = _search_stub([{"name": "OpenAI", "subscribers": 2_000_000}])
    m = TopicMapper(search)
    res = m.map_topic("AI", now=T0, allow_list={"spam"}, deny_list={"spam"})
    assert "spam" not in [n.lower() for n in res.subreddit_names], "deny 应优先于 allow"
    print("✅ test_deny_overrides_allow")


def test_edge_case_fallback_note():
    search = _search_stub([{"name": "OnlyOne", "subscribers": 100}])
    m = TopicMapper(search, min_count=3)
    res = m.map_topic("超窄主题", now=T0)
    assert res.warnings and "边缘 case" in res.warnings[0]
    print("✅ test_edge_case_fallback_note")


def test_cache_hit_within_ttl():
    calls = {"n": 0}
    def counting_search(kw, limit):
        calls["n"] += 1
        return [{"name": "OpenAI", "subscribers": 2_000_000}]
    cache = InMemoryCacheStore()
    m = TopicMapper(counting_search, cache=cache, ttl_days=7)
    r1 = m.map_topic("AI", now=T0)
    assert not r1.from_cache and calls["n"] == 1
    # 3 天后(TTL 内)→ 命中缓存,不再调用 search
    r2 = m.map_topic("AI", now=T0 + timedelta(days=3))
    assert r2.from_cache and not r2.stale and calls["n"] == 1, calls
    assert r2.subreddit_names == r1.subreddit_names
    print("✅ test_cache_hit_within_ttl")


def test_cache_expiry_rederives():
    calls = {"n": 0}
    def counting_search(kw, limit):
        calls["n"] += 1
        return [{"name": "OpenAI", "subscribers": 2_000_000}]
    cache = InMemoryCacheStore()
    m = TopicMapper(counting_search, cache=cache, ttl_days=7)
    m.map_topic("AI", now=T0)
    # 8 天后(超 TTL)→ 重算
    r = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert not r.from_cache and calls["n"] == 2, calls
    print("✅ test_cache_expiry_rederives")


def test_hard_ceiling_not_pushed_on_ttl_refresh():
    cache = InMemoryCacheStore()
    m = TopicMapper(_search_stub([{"name": "OpenAI", "subscribers": 2_000_000}]),
                    cache=cache, ttl_days=7, hard_ceiling_days=30)
    r1 = m.map_topic("AI", now=T0)
    hc1 = r1.hard_ceiling_at
    # 第 8 天 TTL 续期重算 → hard ceiling 必须保持原值(不往后推)
    r2 = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert r2.hard_ceiling_at == hc1, (r2.hard_ceiling_at, hc1)
    # 第 31 天超硬上限 → hard ceiling 重置
    r3 = m.map_topic("AI", now=T0 + timedelta(days=31))
    assert r3.hard_ceiling_at > hc1
    print("✅ test_hard_ceiling_not_pushed_on_ttl_refresh")


def test_no_cache_forces_rederive():
    calls = {"n": 0}
    def counting_search(kw, limit):
        calls["n"] += 1
        return [{"name": "OpenAI", "subscribers": 2_000_000}]
    cache = InMemoryCacheStore()
    m = TopicMapper(counting_search, cache=cache)
    m.map_topic("AI", now=T0)
    m.map_topic("AI", now=T0 + timedelta(days=1), no_cache=True)  # 强刷
    assert calls["n"] == 2, calls
    print("✅ test_no_cache_forces_rederive")


def test_resolve_operator_lists_scope():
    entries = [
        {"list_type": "allow", "subreddit_name": "LocalLLaMA", "scope_topic_id": None},
        {"list_type": "deny", "subreddit_name": "memes", "scope_topic_id": None},
        {"list_type": "allow", "subreddit_name": "OnlyForT7", "scope_topic_id": 7},
    ]
    allow, deny = resolve_operator_lists(entries, topic_id=1)
    assert "localllama" in allow and "onlyfort7" not in allow  # 7 的 scope 不适用 topic 1
    assert "memes" in deny
    allow7, _ = resolve_operator_lists(entries, topic_id=7)
    assert "onlyfort7" in allow7
    print("✅ test_resolve_operator_lists_scope")


def test_quality_monotonic():
    assert _quality_from_subs(None) == 0.5
    assert _quality_from_subs(10) < _quality_from_subs(10_000) < _quality_from_subs(2_000_000)
    print("✅ test_quality_monotonic")


def test_empty_keyword_raises():
    m = TopicMapper(_search_stub([]))
    for bad in ["", "   ", None]:
        try:
            m.map_topic(bad)  # type: ignore
            assert False, f"应对空 keyword 抛错: {bad!r}"
        except ValueError:
            pass
    print("✅ test_empty_keyword_raises")


def test_operator_not_cached_across_calls():
    """🔴 回归(Rex Step3):缓存命中时必须重套本次 operator,不能复用上次的决策。"""
    cache = InMemoryCacheStore()
    search = _search_stub([
        {"name": "OpenAI", "subscribers": 2_000_000},
        {"name": "memes", "subscribers": 5_000_000},
    ])
    m = TopicMapper(search, cache=cache, target_count=5)
    # call 1: allow LocalLLaMA(不在候选)+ deny memes
    r1 = m.map_topic("AI", now=T0, allow_list={"LocalLLaMA"}, deny_list={"memes"})
    n1 = [n.lower() for n in r1.subreddit_names]
    assert "localllama" in n1 and "memes" not in n1
    # call 2: TTL 内命中缓存,但这次 operator 全空 → allow 项消失、被 deny 的候选回归
    r2 = m.map_topic("AI", now=T0 + timedelta(days=1), allow_list=set(), deny_list=set())
    assert r2.from_cache, "应命中缓存(纯候选池)"
    n2 = [n.lower() for n in r2.subreddit_names]
    assert "localllama" not in n2, "上次 allow 决策被缓存污染了"
    assert "memes" in n2, "上次 deny 决策被缓存污染了(memes 应回归候选池)"
    print("✅ test_operator_not_cached_across_calls")


def test_topic_scope_operator_no_leak():
    """topic-scoped operator 不应跨 topic 泄漏(同 keyword 不同 topic_id)。"""
    cache = InMemoryCacheStore()
    search = _search_stub([{"name": "OpenAI", "subscribers": 2_000_000}])
    m = TopicMapper(search, cache=cache, target_count=5)
    entries = [{"list_type": "allow", "subreddit_name": "OnlyForT7", "scope_topic_id": 7}]
    a7, d7 = resolve_operator_lists(entries, topic_id=7)
    r7 = m.map_topic("AI", now=T0, topic_id=7, allow_list=a7, deny_list=d7)
    assert "onlyfort7" in [n.lower() for n in r7.subreddit_names]
    a1, d1 = resolve_operator_lists(entries, topic_id=1)   # T7 的 allow 不适用 T1
    r1 = m.map_topic("AI", now=T0 + timedelta(days=1), topic_id=1, allow_list=a1, deny_list=d1)
    assert r1.from_cache
    assert "onlyfort7" not in [n.lower() for n in r1.subreddit_names], "topic-scoped allow 泄漏到别的 topic"
    print("✅ test_topic_scope_operator_no_leak")


def test_stale_fallback_within_hard_ceiling():
    """🟡(Rex Step3):TTL 过期后若重派生失败,在 hard ceiling 内回退 stale;超出则 fail loud。"""
    calls = {"n": 0}
    def flaky(kw, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"name": "OpenAI", "subscribers": 2_000_000}]
        raise RuntimeError("reddit 503")
    cache = InMemoryCacheStore()
    m = TopicMapper(flaky, cache=cache, ttl_days=7, hard_ceiling_days=30)
    r1 = m.map_topic("AI", now=T0)
    # day 8:TTL 过期,重派生失败,但在 hard ceiling 内 → 回退 stale 缓存
    r2 = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert r2.from_cache and r2.stale, (r2.from_cache, r2.stale)
    assert r2.subreddit_names == r1.subreddit_names
    # staleness 看独立 bool 字段(已断言 .stale)——不再去 warnings 里捞字符串(Rex Step3 🟡)
    # day 31:超 hard ceiling,重派生仍失败 → 不再回退,fail loud
    try:
        m.map_topic("AI", now=T0 + timedelta(days=31))
        assert False, "超 hard ceiling 应 fail loud"
    except RuntimeError:
        pass
    print("✅ test_stale_fallback_within_hard_ceiling")


def test_no_cache_no_stale_fallback():
    """--no-cache 下重派生失败不回退 stale,直接 fail loud。"""
    calls = {"n": 0}
    def flaky(kw, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"name": "OpenAI", "subscribers": 2_000_000}]
        raise RuntimeError("reddit 503")
    cache = InMemoryCacheStore()
    m = TopicMapper(flaky, cache=cache)
    m.map_topic("AI", now=T0)
    try:
        m.map_topic("AI", now=T0 + timedelta(days=1), no_cache=True)
        assert False, "--no-cache 失败应 fail loud"
    except RuntimeError:
        pass
    print("✅ test_no_cache_no_stale_fallback")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
