"""Step 3 落地 topic_resolve 离线决策边界测试(Rex 🟡2)。

monkeypatch `_llm_subreddits` / `_openai_json` / `_verify_subreddit` 避免真网络。
跑法: python3 system1-app/pipeline/tests/test_topic_resolve.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline import topic_resolve as tr  # noqa: E402


def _pop_key():
    return os.environ.pop("OPENAI_API_KEY", None)


def _restore_key(saved):
    if saved is None:
        os.environ.pop("OPENAI_API_KEY", None)
    else:
        os.environ["OPENAI_API_KEY"] = saved


def test_no_openai_key_both_fallback():
    """🐞 无 OPENAI_API_KEY → 版块 + 关键词都回退默认,两个 source 都是 fallback。"""
    saved = _pop_key()
    try:
        r = tr.resolve_topic("anything", ["FB_S1", "FB_S2"], ["fb_k1", "fb_k2"])
        assert r["subreddits"] == ["FB_S1", "FB_S2"]
        assert r["keywords"] == ["fb_k1", "fb_k2"]
        assert r["subreddits_source"] == "fallback"
        assert r["keywords_source"] == "fallback"
    finally:
        _restore_key(saved)
    print("✅ test_no_openai_key_both_fallback")


def test_llm_subs_ok_keywords_fallback_still_llm_subs():
    """🐞 回归(Rex 🔴):LLM 版块成功 + 关键词调用失败 →
    subreddits_source 仍是 llm(松闸不被关键词回退绑架);keywords_source = fallback。"""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit
    tr._llm_subreddits = lambda kw: ["realsub1", "realsub2"]
    tr._openai_json = lambda prompt: None       # 关键词那次 LLM 调用失败
    tr._verify_subreddit = lambda name: True    # 假设版块都真实存在
    try:
        r = tr.resolve_topic("X", ["FB_S"], ["fb_k"])
        assert r["subreddits"] == ["realsub1", "realsub2"]
        assert r["subreddits_source"] == "llm", "版块来自 LLM 必须标 llm,跟关键词解耦"
        assert r["keywords"] == ["fb_k"]
        assert r["keywords_source"] == "fallback"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_llm_subs_ok_keywords_fallback_still_llm_subs")


def test_partial_404_keeps_verified_no_fallback():
    """🐞 部分版块 404(LLM 幻觉)→ 剔除幻觉、保留真实;partial(< target_count)也不回退默认。"""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit
    tr._llm_subreddits = lambda kw: ["a", "fake1", "b", "fake2", "c", "d"]   # 4 真 + 2 假
    tr._openai_json = lambda prompt: {"keywords": ["kw1", "kw2"]}
    fake_404 = {"fake1", "fake2"}
    tr._verify_subreddit = lambda n: n not in fake_404
    try:
        r = tr.resolve_topic("X", ["FB_S"], ["fb_k"])
        assert set(r["subreddits"]) == {"a", "b", "c", "d"}, r["subreddits"]
        assert len(r["subreddits"]) < 6, "partial<target 时不应凑满"
        assert r["subreddits_source"] == "llm", "partial 仍算 LLM 来源(只要有 verified 就不回退)"
        assert r["keywords"] == ["kw1", "kw2"]
        assert r["keywords_source"] == "llm"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_partial_404_keeps_verified_no_fallback")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
