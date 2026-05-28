"""Step 3 topic mapping — tests (stub-injected, no network / API dependency).

Run: python3 system1-app/pipeline/tests/test_topic_mapping.py
(Plain asserts, no pytest required; test_* function names are also pytest-discoverable.)
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
    # High subscribers + early position → ranks higher; tinysub should be truncated
    assert "tinysub" not in res.subreddit_names
    # Scores are monotonically non-increasing
    scores = [c.score for c in res.subreddits]
    assert scores == sorted(scores, reverse=True), scores
    print("✅ test_basic_ranking_and_count")


def test_llm_and_synonym_merge():
    search = _search_stub([{"name": "startups", "subscribers": 1_500_000}])
    llm = lambda kw: ["Entrepreneur", "startups"]  # startups overlaps with search → merge source
    m = TopicMapper(search, llm_suggest_fn=llm, target_count=5)
    res = m.map_topic("entrepreneurship", now=T0)
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
    res = m.map_topic("AI", now=T0)  # Should not raise; degrades to search-only candidates
    assert res.subreddit_names == ["OpenAI"]
    print("✅ test_llm_failure_degrades")


def test_operator_allow_deny():
    search = _search_stub([
        {"name": "OpenAI", "subscribers": 2_000_000},
        {"name": "memes", "subscribers": 5_000_000},   # High quality but should be denied
    ])
    m = TopicMapper(search, target_count=5)
    res = m.map_topic(
        "AI", now=T0,
        allow_list={"LocalLLaMA"},   # Not in candidates → forced inclusion
        deny_list={"memes"},         # Permanently dropped
    )
    names = {c.name.lower() for c in res.subreddits}
    assert "memes" not in names, "deny did not take effect"
    assert "localllama" in names, "allow did not force inclusion"
    forced = next(c for c in res.subreddits if c.name.lower() == "localllama")
    assert forced.forced and forced.score == 1.0
    assert "memes" in [n.lower() for n in res.deny_list_applied]
    print("✅ test_operator_allow_deny")


def test_deny_overrides_allow():
    search = _search_stub([{"name": "OpenAI", "subscribers": 2_000_000}])
    m = TopicMapper(search)
    res = m.map_topic("AI", now=T0, allow_list={"spam"}, deny_list={"spam"})
    assert "spam" not in [n.lower() for n in res.subreddit_names], "deny should override allow"
    print("✅ test_deny_overrides_allow")


def test_edge_case_fallback_note():
    search = _search_stub([{"name": "OnlyOne", "subscribers": 100}])
    m = TopicMapper(search, min_count=3)
    res = m.map_topic("ultra narrow topic", now=T0)
    assert res.warnings and "edge case" in res.warnings[0]
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
    # 3 days later (within TTL) → cache hit, search not called again
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
    # 8 days later (TTL exceeded) → recompute
    r = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert not r.from_cache and calls["n"] == 2, calls
    print("✅ test_cache_expiry_rederives")


def test_hard_ceiling_not_pushed_on_ttl_refresh():
    cache = InMemoryCacheStore()
    m = TopicMapper(_search_stub([{"name": "OpenAI", "subscribers": 2_000_000}]),
                    cache=cache, ttl_days=7, hard_ceiling_days=30)
    r1 = m.map_topic("AI", now=T0)
    hc1 = r1.hard_ceiling_at
    # Day 8: TTL refresh recompute → hard ceiling must keep the original value (not pushed back)
    r2 = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert r2.hard_ceiling_at == hc1, (r2.hard_ceiling_at, hc1)
    # Day 31: past hard ceiling → hard ceiling is reset
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
    m.map_topic("AI", now=T0 + timedelta(days=1), no_cache=True)  # Force refresh
    assert calls["n"] == 2, calls
    print("✅ test_no_cache_forces_rederive")


def test_resolve_operator_lists_scope():
    entries = [
        {"list_type": "allow", "subreddit_name": "LocalLLaMA", "scope_topic_id": None},
        {"list_type": "deny", "subreddit_name": "memes", "scope_topic_id": None},
        {"list_type": "allow", "subreddit_name": "OnlyForT7", "scope_topic_id": 7},
    ]
    allow, deny = resolve_operator_lists(entries, topic_id=1)
    assert "localllama" in allow and "onlyfort7" not in allow  # scope 7 doesn't apply to topic 1
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
            assert False, f"should raise on empty keyword: {bad!r}"
        except ValueError:
            pass
    print("✅ test_empty_keyword_raises")


def test_operator_not_cached_across_calls():
    """🔴 Regression (Rex Step 3): on cache hit, this call's operator must be re-applied; previous decisions must not be reused."""
    cache = InMemoryCacheStore()
    search = _search_stub([
        {"name": "OpenAI", "subscribers": 2_000_000},
        {"name": "memes", "subscribers": 5_000_000},
    ])
    m = TopicMapper(search, cache=cache, target_count=5)
    # call 1: allow LocalLLaMA (not in candidates) + deny memes
    r1 = m.map_topic("AI", now=T0, allow_list={"LocalLLaMA"}, deny_list={"memes"})
    n1 = [n.lower() for n in r1.subreddit_names]
    assert "localllama" in n1 and "memes" not in n1
    # call 2: cache hit within TTL, but this call's operator is empty → allow entry disappears,
    # the previously denied candidate returns
    r2 = m.map_topic("AI", now=T0 + timedelta(days=1), allow_list=set(), deny_list=set())
    assert r2.from_cache, "should hit cache (pure candidate pool)"
    n2 = [n.lower() for n in r2.subreddit_names]
    assert "localllama" not in n2, "previous allow decision leaked through the cache"
    assert "memes" in n2, "previous deny decision leaked through the cache (memes should return to the candidate pool)"
    print("✅ test_operator_not_cached_across_calls")


def test_topic_scope_operator_no_leak():
    """A topic-scoped operator must not leak across topics (same keyword, different topic_id)."""
    cache = InMemoryCacheStore()
    search = _search_stub([{"name": "OpenAI", "subscribers": 2_000_000}])
    m = TopicMapper(search, cache=cache, target_count=5)
    entries = [{"list_type": "allow", "subreddit_name": "OnlyForT7", "scope_topic_id": 7}]
    a7, d7 = resolve_operator_lists(entries, topic_id=7)
    r7 = m.map_topic("AI", now=T0, topic_id=7, allow_list=a7, deny_list=d7)
    assert "onlyfort7" in [n.lower() for n in r7.subreddit_names]
    a1, d1 = resolve_operator_lists(entries, topic_id=1)   # T7's allow doesn't apply to T1
    r1 = m.map_topic("AI", now=T0 + timedelta(days=1), topic_id=1, allow_list=a1, deny_list=d1)
    assert r1.from_cache
    assert "onlyfort7" not in [n.lower() for n in r1.subreddit_names], "topic-scoped allow leaked to another topic"
    print("✅ test_topic_scope_operator_no_leak")


def test_stale_fallback_within_hard_ceiling():
    """🟡 (Rex Step 3): after TTL expiry, if re-derivation fails, fall back to stale within the hard ceiling;
    past the ceiling, fail loud."""
    calls = {"n": 0}
    def flaky(kw, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"name": "OpenAI", "subscribers": 2_000_000}]
        raise RuntimeError("reddit 503")
    cache = InMemoryCacheStore()
    m = TopicMapper(flaky, cache=cache, ttl_days=7, hard_ceiling_days=30)
    r1 = m.map_topic("AI", now=T0)
    # Day 8: TTL expired, re-derivation fails, but within the hard ceiling → fall back to stale cache
    r2 = m.map_topic("AI", now=T0 + timedelta(days=8))
    assert r2.from_cache and r2.stale, (r2.from_cache, r2.stale)
    assert r2.subreddit_names == r1.subreddit_names
    # staleness lives on a dedicated bool field (already asserted via .stale) — don't fish it out of warnings (Rex Step 3 🟡)
    # Day 31: past the hard ceiling, re-derivation still fails → no fallback, fail loud
    try:
        m.map_topic("AI", now=T0 + timedelta(days=31))
        assert False, "past the hard ceiling should fail loud"
    except RuntimeError:
        pass
    print("✅ test_stale_fallback_within_hard_ceiling")


def test_no_cache_no_stale_fallback():
    """Under --no-cache, a re-derivation failure does NOT fall back to stale; fail loud directly."""
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
        assert False, "--no-cache failure should fail loud"
    except RuntimeError:
        pass
    print("✅ test_no_cache_no_stale_fallback")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
