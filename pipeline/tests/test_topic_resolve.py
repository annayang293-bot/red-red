"""Step 3 landing layer (topic_resolve) offline decision-boundary tests (Rex 🟡2).

monkeypatch `_llm_subreddits` / `_openai_json` / `_verify_subreddit` to avoid real network calls.
Run: python3 system1-app/pipeline/tests/test_topic_resolve.py
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr

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


class _FakeResp:
    """Minimal fake of requests.Response for _verify_subreddit tests."""

    def __init__(self, status_code: int, data: dict | None = None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return {"data": self._data}


def test_verify_subreddit_drops_small_subscribers():
    """🐞 (Anna 2026-05-28): r/X with subscribers < MIN_SUBSCRIBERS → drop, even if it exists.
    Prevents LLM picks like r/AI_Entrepreneur (2k subs) from beating r/OpenAI (2.8M)."""
    import requests
    orig_get = requests.get
    fake_subs = {"big": 2_000_000, "tiny": 5_000}
    requests.get = lambda url, **kw: _FakeResp(200, {"subscribers": fake_subs[url.split("/r/")[1].split("/")[0]]})
    try:
        assert tr._verify_subreddit("big") is True
        assert tr._verify_subreddit("tiny") is False, "small sub must be dropped"
        # explicit threshold override
        assert tr._verify_subreddit("tiny", min_subscribers=1000) is True, "with lower threshold, small sub passes"
    finally:
        requests.get = orig_get
    print("✅ test_verify_subreddit_drops_small_subscribers")


def test_verify_subreddit_keeps_when_subscribers_unknown():
    """🐞 about.json returns 200 but no `subscribers` field (rare but possible) → keep (fail-safe;
    we don't have enough info to drop, the main fetch will catch true failures)."""
    import requests
    orig_get = requests.get
    requests.get = lambda url, **kw: _FakeResp(200, {})  # No subscribers field
    try:
        assert tr._verify_subreddit("noinfo") is True
    finally:
        requests.get = orig_get
    print("✅ test_verify_subreddit_keeps_when_subscribers_unknown")


def test_verify_subreddit_drops_private_restricted():
    """🐞 banned / private / restricted = inaccessible → drop (the main fetch would fail anyway)."""
    import requests
    orig_get = requests.get
    cases = [("b", "banned"), ("p", "private"), ("r", "restricted")]
    for name, typ in cases:
        def fake(url, _typ=typ, **kw):
            return _FakeResp(200, {"subreddit_type": _typ, "subscribers": 5_000_000})
        requests.get = fake
        try:
            assert tr._verify_subreddit(name) is False, f"{typ} subreddit must be dropped"
        finally:
            requests.get = orig_get
    print("✅ test_verify_subreddit_drops_private_restricted")


def test_llm_subreddits_parses_structured_response():
    """🐞 (Anna 2026-05-28 prompt v3): _llm_subreddits parses {core_intent, core_noun, modifier_intent,
    subreddits}; stashes the primary+modifier classification for later logging;
    returns plain list (TopicMapper.llm_suggest_fn contract unchanged)."""
    orig_openai = tr._openai_json
    tr._openai_json = lambda prompt: {
        "core_intent": "A",
        "core_noun": "创业",
        "modifier_intent": "C",
        "subreddits": ["SaaS", "Entrepreneur", "indiehackers", "OpenAI"],
    }
    try:
        # Clear any stashed state from earlier tests in this run
        tr._last_intent.clear()
        out = tr._llm_subreddits("AI 创业")
        assert out == ["SaaS", "Entrepreneur", "indiehackers", "OpenAI"], out
        # Classification should be stashed for resolve_topic to log: primary + modifier
        stashed = tr._last_intent.get("AI 创业", "")
        assert stashed.startswith("A ("), stashed
        assert "创业" in stashed
        assert "modifier=C" in stashed, "modifier intent should be captured"
    finally:
        tr._openai_json = orig_openai
        tr._last_intent.clear()
    print("✅ test_llm_subreddits_parses_structured_response")


def test_llm_subreddits_injects_user_hint():
    """🐞 (Anna 2026-05-28 option 3): when a user hint is passed, it gets pinned at the top of
    the prompt so the LLM treats it as a hard constraint. Verify by capturing the prompt
    text sent to _openai_json."""
    captured = {}
    orig_openai = tr._openai_json
    def fake(prompt):
        captured["prompt"] = prompt
        return {"core_intent": "A", "core_noun": "教程", "modifier_intent": "C",
                "subreddits": ["learnpython", "Python"]}
    tr._openai_json = fake
    try:
        tr._last_intent.clear()
        out = tr._llm_subreddits("Claude 教程", hint="重点 API 用法,不是聊天机器人玩法")
        assert out == ["learnpython", "Python"], out
        assert "用户额外提示" in captured["prompt"], "hint header missing from prompt"
        assert "API 用法" in captured["prompt"], "hint text missing from prompt"
        # The hint must appear BEFORE the step-1 classification block, so the LLM reads it first.
        idx_hint = captured["prompt"].index("用户额外提示")
        idx_step1 = captured["prompt"].index("第 1 步")
        assert idx_hint < idx_step1, "hint should be pinned above the step-1 instructions"
    finally:
        tr._openai_json = orig_openai
        tr._last_intent.clear()
    print("✅ test_llm_subreddits_injects_user_hint")


def test_llm_subreddits_no_hint_means_no_hint_block():
    """🐞 No hint = no '用户额外提示' header in the prompt (keeps the prompt clean for the
    common case)."""
    captured = {}
    orig_openai = tr._openai_json
    def fake(prompt):
        captured["prompt"] = prompt
        return {"core_intent": "A", "subreddits": ["X"]}
    tr._openai_json = fake
    try:
        tr._last_intent.clear()
        tr._llm_subreddits("AI 创业")  # No hint kwarg
        assert "用户额外提示" not in captured["prompt"], "no hint = no header"
    finally:
        tr._openai_json = orig_openai
        tr._last_intent.clear()
    print("✅ test_llm_subreddits_no_hint_means_no_hint_block")


def test_resolve_topic_passes_hint_through():
    """🐞 resolve_topic's hint kwarg reaches _llm_subreddits and logs a 'applied user hint' line."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit

    hints_seen = []
    def fake_llm(kw, *, hint=None):
        hints_seen.append(hint)
        return ["sub1", "sub2"]
    tr._llm_subreddits = fake_llm
    tr._openai_json = lambda prompt: {"keywords": ["kw1"]}
    tr._verify_subreddit = lambda n: True
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            tr.resolve_topic("Claude 教程", ["FB"], ["fb"], hint="重点 API 用法")
        assert hints_seen == ["重点 API 用法"], hints_seen
        assert "applied user hint" in err.getvalue(), err.getvalue()
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_resolve_topic_passes_hint_through")


def test_llm_subreddits_tolerates_legacy_response_shape():
    """🐞 LLM returning the old shape {subreddits: [...]} (no classification) still works —
    we extract the subs and skip the classification log line."""
    orig_openai = tr._openai_json
    tr._openai_json = lambda prompt: {"subreddits": ["X", "Y"]}  # No core_intent
    try:
        tr._last_intent.clear()
        out = tr._llm_subreddits("anything")
        assert out == ["X", "Y"], out
        assert "anything" not in tr._last_intent, "no intent → don't stash"
    finally:
        tr._openai_json = orig_openai
        tr._last_intent.clear()
    print("✅ test_llm_subreddits_tolerates_legacy_response_shape")


def test_resolve_topic_logs_classification():
    """🐞 (Anna 2026-05-28): resolve_topic surfaces the LLM's classification to stderr so the
    operator can verify the topic was understood (the failure mode where LLM tagged 'AI 创业'
    as B is silent without this signal). v3: also surfaces the modifier intent."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit

    # Use real _llm_subreddits so it stashes intent — but stub _openai_json (both subs and keywords calls).
    def fake_openai(prompt):
        if "core_intent" in prompt:
            return {"core_intent": "A", "core_noun": "创业", "modifier_intent": "C",
                    "subreddits": ["SaaS", "Entrepreneur", "OpenAI"]}
        return {"keywords": ["startup", "saas"]}
    tr._openai_json = fake_openai
    tr._verify_subreddit = lambda n: True
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            tr.resolve_topic("AI 创业", ["FB"], ["fb"])
        log = err.getvalue()
        assert "classified 'AI 创业' as content type: A" in log, f"classification not logged: {log!r}"
        assert "创业" in log
        assert "modifier=C" in log, "modifier intent should be in the log line"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_resolve_topic_logs_classification")


def test_mega_sub_injection_for_ai_keyword():
    """🐞 (Anna 2026-05-28 absolute rule): for AI-adjacent keywords, r/OpenAI or r/ArtificialIntelligence
    MUST land in the final mapping — even if the LLM didn't pick them and even if the user hint
    suppressed them. Mega-sub injection runs post-verify, before target_count truncation."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit

    # LLM gives 6 entrepreneurship subs but completely skips OpenAI / ArtificialIntelligence
    # (mimics the failure mode where a broad hint suppressed AI flagships).
    tr._llm_subreddits = lambda kw, hint=None: [
        "Entrepreneur", "startups", "indiehackers", "SaaS", "SideProject", "SmallBusiness"
    ]
    tr._openai_json = lambda prompt: {"keywords": ["startup"]}
    tr._verify_subreddit = lambda n: True
    try:
        r = tr.resolve_topic("AI 创业", ["FB"], ["fb"])
        subs = r["subreddits"]
        # OpenAI (or ArtificialIntelligence) must be in the final list — that's the absolute rule.
        ai_flag = "openai" in (s.lower() for s in subs) or "artificialintelligence" in (s.lower() for s in subs)
        assert ai_flag, f"AI mega-sub injection must put OpenAI/AI in final list; got {subs}"
        # Target_count=6 by default — list shouldn't blow past that.
        assert len(subs) <= 6, f"list should not exceed target_count: {subs}"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_mega_sub_injection_for_ai_keyword")


def test_mega_sub_injection_skips_when_already_present():
    """🐞 If LLM already picked the mandatory sub, injection is a no-op (no duplicates).
    Demonstrates the dedup path."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit

    tr._llm_subreddits = lambda kw, hint=None: ["OpenAI", "SaaS", "Entrepreneur", "startups"]
    tr._openai_json = lambda prompt: {"keywords": ["startup"]}
    tr._verify_subreddit = lambda n: True
    try:
        r = tr.resolve_topic("AI 创业", ["FB"], ["fb"])
        subs = r["subreddits"]
        # Count OpenAI — must be exactly 1 (no duplicate from injection)
        assert sum(1 for s in subs if s.lower() == "openai") == 1, subs
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_mega_sub_injection_skips_when_already_present")


def test_mega_sub_injection_word_boundary_no_false_positive():
    """🐞 ASCII triggers use \\b boundaries — 'ai' must NOT match 'paint', 'hair', etc.
    Without this, the rule would inject OpenAI on any topic containing those substrings."""
    # 'paint' contains 'ai' as a substring — but with \b boundary, the trigger shouldn't fire.
    assert not tr._keyword_matches_trigger("paint art", "ai"), "'ai' should NOT match 'paint'"
    assert not tr._keyword_matches_trigger("hairstyle", "ai"), "'ai' should NOT match 'hairstyle'"
    # Standalone 'ai' must match.
    assert tr._keyword_matches_trigger("AI startup", "ai"), "standalone 'ai' should match"
    assert tr._keyword_matches_trigger("ChatGPT 教程", "chatgpt"), "'chatgpt' should match"
    # CJK substring: '人工智能创业' contains the CJK trigger '人工智能'.
    assert tr._keyword_matches_trigger("人工智能创业", "人工智能")
    print("✅ test_mega_sub_injection_word_boundary_no_false_positive")


def test_mega_sub_injection_inactive_for_unrelated_topic():
    """🐞 No injection for topics that don't match any trigger (e.g. 'knitting')."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit

    tr._llm_subreddits = lambda kw, hint=None: ["knitting", "Crochet", "yarn", "craftparties"]
    tr._openai_json = lambda prompt: {"keywords": ["yarn"]}
    tr._verify_subreddit = lambda n: True
    try:
        r = tr.resolve_topic("knitting patterns", ["FB"], ["fb"])
        assert "OpenAI" not in r["subreddits"], "no AI injection for unrelated topic"
        assert "Python" not in r["subreddits"], "no Python injection"
        assert "gaming" not in r["subreddits"], "no gaming injection"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_mega_sub_injection_inactive_for_unrelated_topic")


def test_quality_count_below_threshold_warns_keeps_results():
    """🐞 (Anna 2026-05-28): if quality verification leaves <MIN_QUALITY_COUNT subs, surface a
    loud warning to stderr — but DO use whatever we got (don't silently fall back to AI defaults
    for a non-AI topic; the operator should manually curate topics_cache instead)."""
    saved = _pop_key()
    os.environ["OPENAI_API_KEY"] = "dummy"
    orig_llm, orig_openai, orig_verify = tr._llm_subreddits, tr._openai_json, tr._verify_subreddit
    tr._llm_subreddits = lambda kw: ["only1", "only2", "tiny1", "tiny2"]
    tr._openai_json = lambda prompt: {"keywords": ["kw1"]}
    survives = {"only1", "only2"}  # 2 quality, others dropped — below MIN_QUALITY_COUNT=4
    tr._verify_subreddit = lambda n: n in survives
    try:
        err = io.StringIO()
        with redirect_stderr(err):
            r = tr.resolve_topic("actor", ["AI_DEFAULT_S"], ["fb_k"])
        log = err.getvalue()
        assert "topic mapping incomplete" in log, f"warning not surfaced: {log!r}"
        assert "only 2" in log
        assert set(r["subreddits"]) == survives, "must keep the 2 verified, not fall back to AI defaults"
        assert "AI_DEFAULT_S" not in r["subreddits"], "must NOT pollute non-AI topic with AI fallback"
        assert r["subreddits_source"] == "llm"
    finally:
        tr._llm_subreddits, tr._openai_json, tr._verify_subreddit = orig_llm, orig_openai, orig_verify
        _restore_key(saved)
    print("✅ test_quality_count_below_threshold_warns_keeps_results")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
