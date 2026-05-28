"""Step 3 landing layer (topic_resolve) offline decision-boundary tests (Rex 🟡2).

monkeypatch `_llm_subreddits` / `_openai_json` / `_verify_subreddit` to avoid real network calls.
Run: python3 system1-app/pipeline/tests/test_topic_resolve.py
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
    """🐞 No OPENAI_API_KEY → both subreddits + keywords fall back to defaults, both sources = fallback."""
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
    """🐞 Regression (Rex 🔴): LLM subreddits succeed + keyword call fails →
    subreddits_source must still be llm (gate relaxation not held hostage by keyword fallback);
    keywords_source = fallback."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit
    tr._llm_subreddits = lambda kw: ["realsub1", "realsub2"]
    tr._openai_json = lambda prompt: None       # The keywords LLM call fails
    tr._verify_subreddit = lambda name: True    # Assume all subreddits really exist
    try:
        r = tr.resolve_topic("X", ["FB_S"], ["fb_k"])
        assert r["subreddits"] == ["realsub1", "realsub2"]
        assert r["subreddits_source"] == "llm", "LLM-derived subreddits must be tagged llm, decoupled from keywords"
        assert r["keywords"] == ["fb_k"]
        assert r["keywords_source"] == "fallback"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_llm_subs_ok_keywords_fallback_still_llm_subs")


def test_partial_404_keeps_verified_no_fallback():
    """🐞 Partial 404 (LLM hallucinations) → drop the hallucinations, keep the real ones;
    even partial (< target_count) does NOT fall back to defaults."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit
    tr._llm_subreddits = lambda kw: ["a", "fake1", "b", "fake2", "c", "d"]   # 4 real + 2 fake
    tr._openai_json = lambda prompt: {"keywords": ["kw1", "kw2"]}
    fake_404 = {"fake1", "fake2"}
    tr._verify_subreddit = lambda n: n not in fake_404
    try:
        r = tr.resolve_topic("X", ["FB_S"], ["fb_k"])
        assert set(r["subreddits"]) == {"a", "b", "c", "d"}, r["subreddits"]
        assert len(r["subreddits"]) < 6, "partial<target should not be padded up"
        assert r["subreddits_source"] == "llm", "partial still counts as LLM source (as long as something verified, no fallback)"
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
