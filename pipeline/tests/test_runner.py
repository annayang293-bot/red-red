"""Step 4 pipeline runner — end-to-end tests (stub sources, no network / OpenAI required).

Run: python3 system1-app/pipeline/tests/test_runner.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.schema import HotItem, make_id, canonical_url, now_iso, to_iso  # noqa: E402
from pipeline.sources.base import Source  # noqa: E402
from pipeline.runner import run_pipeline, config_fingerprint, build_topic_sources  # noqa: E402
from pipeline.config import DEFAULT_CONFIG  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


def mk(source, nid, title, likes, comments, hours_ago, url=None):
    url = url or f"https://example.com/{source}/{nid}"
    pub = NOW - timedelta(hours=hours_ago)
    return HotItem(
        id=make_id(source, nid), dedup_key=canonical_url(url), title=title,
        source=source, source_native_id=nid, url=url, author="u",
        published_at=to_iso(pub), captured_at=NOW.isoformat(), lang="en",
        media_type="text", raw_metrics={"likes": likes, "comments": comments, "saves": 0},
        source_native={"subreddit": "test"}, tags=["test"], raw_snippet=title)


class StubSource(Source):
    def __init__(self, name, items):
        self._name = name
        self._items = items
        self.name = name

    def fetch(self):
        return list(self._items)


class BoomSource(Source):
    name = "boom"

    def __init__(self):
        pass

    def fetch(self):
        raise RuntimeError("network down")


# 18 relevant AI posts (each contains ≥2 keywords → max relevance), engagement on a decreasing
# gradient; + 2 unrelated high-engagement posts (rel=0, should be filtered by the relevance gate).
# Real pipeline N ≈ 390; supplying enough N here keeps the "Top 20%" gate from degenerating
# (small N + a single outlier would clamp the gate down to a single item).
_AI_FRAGS = [
    "AI agent", "AI startup", "AI SaaS", "AI coding tool", "LLM app", "GPT product",
    "Claude automation", "AI founder", "indie AI tool", "AI revenue", "AI growth hack",
    "AI pricing", "AI product launch", "AI for customers", "AI monetization",
    "AI MRR", "AI build in public", "AI side project",
]


def _reddit_items():
    items = []
    base = 2000
    for i, frag in enumerate(_AI_FRAGS):
        likes = base - i * 100
        items.append(mk("reddit", f"r{i}", f"{frag}: how I did it #{i}",
                        likes, likes // 4, 2 + i))
    # Unrelated high-engagement (medium scale, doesn't dominate normalization; filtered by the relevance gate)
    items.append(mk("reddit", "off1", "cooking pasta recipe at home", 700, 150, 1))
    items.append(mk("reddit", "off2", "my cat is so cute today", 650, 120, 1))
    return items


def _ph_items():
    # PH zero-engagement (likes/comments=0) → hot 0; surfaces only via quota
    return [
        mk("product_hunt", "ph1", "Nota: AI Notes & Voice tool", 0, 0, 3),
        mk("product_hunt", "ph2", "AI Outreach SaaS launch", 0, 0, 6),
        mk("product_hunt", "ph3", "AI product roadmap app", 0, 0, 9),
    ]


def test_end_to_end_basic():
    reddit = StubSource("reddit", _reddit_items())
    ph = StubSource("product_hunt", _ph_items())
    res = run_pipeline("AI startup", [reddit, ph], now=NOW)

    assert res.status == "completed"
    assert res.candidates_count == 23, res.candidates_count   # 20 reddit + 3 PH
    assert res.scored_count >= 3, res.scored_count
    assert res.top_count >= 4, res.top_count
    # Every top row has tier + comment + config_fingerprint
    for row in res.top:
        assert row["tier"] in ("强迁移", "中等迁移", "弱迁移"), row
        assert row["comment"]
        assert row["item"].source_native.get("config_fingerprint") == res.config_fingerprint
    # Unrelated posts (pasta/cat, low rel) should not be in top
    assert all(
        "pasta" not in r["item"].title and "cat" not in r["item"].title
        for r in res.top
    )
    # All three tiers should appear (when top is large enough)
    tiers = {r["tier"] for r in res.top}
    assert "强迁移" in tiers, tiers
    print(f"✅ test_end_to_end_basic (candidates={res.candidates_count} "
          f"scored={res.scored_count} top={res.top_count} tiers={tiers} ai={res.ai_mode})")


def test_ph_quota_surfaces_zero_engagement():
    """PH zero-engagement gets filtered by the hot gate, but surfaces in top via the quota (=2)."""
    reddit = StubSource("reddit", _reddit_items())
    ph = StubSource("product_hunt", _ph_items())
    res = run_pipeline("AI startup", [reddit, ph], now=NOW)
    ph_in_top = [r for r in res.top if r["item"].source == "product_hunt"]
    assert 1 <= len(ph_in_top) <= 2, f"PH quota should be ≤2 with surfacing; got {len(ph_in_top)}"
    print(f"✅ test_ph_quota_surfaces_zero_engagement (PH in top = {len(ph_in_top)})")


def test_failed_source_isolated():
    reddit = StubSource("reddit", _reddit_items())
    res = run_pipeline("AI startup", [reddit, BoomSource()], now=NOW)
    assert res.status == "completed"            # Single source failure doesn't tank the run
    assert "boom" in res.failed_sources, res.failed_sources
    assert any("source_fetch_failed" in a for a in res.sanity["anomalies"])
    assert res.top_count >= 1                    # Reddit still produces results
    print("✅ test_failed_source_isolated")


def test_empty_sources_sane():
    res = run_pipeline("AI startup", [StubSource("reddit", [])], now=NOW)
    assert res.status == "completed"
    assert res.candidates_count == 0
    assert res.top_count == 0
    assert any("empty_report" in a for a in res.sanity["anomalies"])
    print("✅ test_empty_sources_sane")


def test_fingerprint_stable():
    fp1 = config_fingerprint(DEFAULT_CONFIG, DEFAULT_CONFIG["keywords"])
    fp2 = config_fingerprint(DEFAULT_CONFIG, list(reversed(DEFAULT_CONFIG["keywords"])))
    assert fp1 == fp2, "keyword order should not change the fingerprint (internal sort)"
    assert fp1.startswith("cfg_")
    print("✅ test_fingerprint_stable")


def test_relevance_gate_filters_offtopic():
    """All off-topic posts → relevance all 0 → don't pass the gate → empty report."""
    off = [mk("reddit", f"o{i}", "cooking pasta recipe", 500, 100, 2) for i in range(6)]
    res = run_pipeline("AI startup", [StubSource("reddit", off)], now=NOW)
    assert res.top_count == 0, "unrelated content should not enter top"
    print("✅ test_relevance_gate_filters_offtopic")


class _CapReddit(Source):
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = "reddit"

    def fetch(self):
        return []


class _CapPH(Source):
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = "product_hunt"

    def fetch(self):
        return []


class _FakeMapper:
    def map_topic(self, topic, **kw):
        class M:
            subreddit_names = ["OpenAI", "startups"]
        return M()


def test_build_topic_sources_respects_cfg():
    """🔴 Regression (Rex Step 4): build_topic_sources must be cfg-driven; do not hardcode PH=rss."""
    cfg = {**DEFAULT_CONFIG,
           "product_hunt": {"auth_mode": "token"},
           "reddit": {"auth_mode": "oauth"}}
    sources, mapping = build_topic_sources(
        "AI startup", _FakeMapper(), cfg, reddit_cls=_CapReddit, ph_cls=_CapPH)
    ph = next(s for s in sources if s.name == "product_hunt")
    assert ph.cfg["product_hunt"]["auth_mode"] == "token", ph.cfg   # Not overridden by rss
    rd = next(s for s in sources if s.name == "reddit")
    assert rd.cfg["reddit"]["auth_mode"] == "oauth"
    assert rd.cfg["reddit"]["subreddits"] == ["OpenAI", "startups"]
    print("✅ test_build_topic_sources_respects_cfg")


def test_all_sources_fail_status_failed():
    """All sources fail + zero candidates → status=failed (distinct from 'ran successfully but empty')."""
    res = run_pipeline("AI startup", [BoomSource()], now=NOW)
    assert res.status == "failed", res.status
    assert "boom" in res.failed_sources
    print("✅ test_all_sources_fail_status_failed")


def test_ai_meta_missing_flagged():
    """🟡 (Rex Step 4): review_fn misses some items → sanity flags ai_meta_missing, not silently half-empty."""
    reddit = StubSource("reddit", _reddit_items())
    res = run_pipeline("AI startup", [reddit], now=NOW,
                       review_fn=lambda items, cfg: ({}, "ai"))  # Empty meta
    assert res.top_count >= 1
    assert any("ai_meta_missing" in a for a in res.sanity["anomalies"]), res.sanity["anomalies"]
    print("✅ test_ai_meta_missing_flagged")


def test_enrich_top_with_transcripts_attaches_text_to_video_posts():
    """🐞 Regression (Anna 2026-06-01): for Top-N Reddit `hosted:video` posts, the runner
    transcribes the audio via `pipeline.transcribe` and stashes text + language + cost on
    `source_native`. Non-video posts are skipped. Failures don't crash — they bump the
    failure counter returned by `_enrich_top_with_transcripts`, which then flows into
    sanity_check's `video_transcribe_failed:N` anomaly.
    """
    from pipeline.runner import _enrich_top_with_transcripts
    from unittest.mock import patch

    def mk_item(nid, *, video, content_url=""):
        return HotItem(
            id=make_id("reddit", nid),
            dedup_key=canonical_url(f"https://www.reddit.com/r/x/comments/{nid}/"),
            title=f"Post {nid}",
            source="reddit",
            source_native_id=nid,
            url=f"https://www.reddit.com/r/x/comments/{nid}/",
            author="u", published_at=to_iso(NOW), captured_at=NOW.isoformat(),
            lang="en", media_type="text",
            raw_metrics={"likes": 1, "upvotes": 1, "comments": 0, "saves": 0},
            source_native={
                "subreddit": "x",
                "post_type": "hosted:video" if video else "self",
                "content_url": content_url,
            },
            tags=["x"], raw_snippet="",
        )

    video_ok = mk_item("v1", video=True, content_url="https://v.redd.it/abc")
    video_fail = mk_item("v2", video=True, content_url="https://v.redd.it/def")
    plain_text = mk_item("t1", video=False)

    call_log: list[str] = []

    def fake_transcribe_reddit_video(source_native):
        cu = source_native.get("content_url", "")
        call_log.append(cu)
        if "abc" in cu:
            return {"text": "OK transcript", "language": "english",
                    "duration_seconds": 60.0, "cost_usd": 0.006}
        return None   # simulate audio fetch or Whisper failure for the second video

    with patch("pipeline.transcribe.transcribe_reddit_video", side_effect=fake_transcribe_reddit_video):
        failed = _enrich_top_with_transcripts([video_ok, video_fail, plain_text], {})

    assert failed == 1, failed
    # Non-video item never invoked the transcriber.
    assert call_log == ["https://v.redd.it/abc", "https://v.redd.it/def"], call_log
    # OK video got transcript + language + cost on source_native.
    assert video_ok.source_native["transcript"] == "OK transcript"
    assert video_ok.source_native["transcript_lang"] == "english"
    assert video_ok.source_native["transcript_cost_usd"] == 0.006
    # Failed video: source_native unchanged (no transcript key written).
    assert "transcript" not in (video_fail.source_native or {})
    # Plain text: source_native unchanged (no transcript key written).
    assert "transcript" not in (plain_text.source_native or {})
    print("✅ test_enrich_top_with_transcripts_attaches_text_to_video_posts")


def test_sanity_check_surfaces_video_transcribe_failed():
    from pipeline.runner import sanity_check
    items = _reddit_items()[:5]
    s = sanity_check(items, ai_mode="ai", failed_sources=[],
                     ai_meta_missing=0, video_transcribe_failed=2)
    assert any("video_transcribe_failed:2" in a for a in s["anomalies"]), s["anomalies"]
    assert s["status"] == "OK_WITH_ANOMALY"
    # 0 failures → no anomaly added on that axis
    s2 = sanity_check(items, ai_mode="ai", failed_sources=[],
                      ai_meta_missing=0, video_transcribe_failed=0)
    assert not any("video_transcribe_failed" in a for a in s2["anomalies"]), s2["anomalies"]
    print("✅ test_sanity_check_surfaces_video_transcribe_failed")


def test_enrich_top_with_transcripts_can_be_disabled_via_cfg():
    """`cfg.transcribe.enabled = False` short-circuits the loop without calling the module —
    useful for prod kill-switching if Whisper rates spike."""
    from pipeline.runner import _enrich_top_with_transcripts
    from unittest.mock import patch
    item = HotItem(
        id=make_id("reddit", "v1"),
        dedup_key=canonical_url("https://www.reddit.com/r/x/comments/v1/"),
        title="t", source="reddit", source_native_id="v1",
        url="https://www.reddit.com/r/x/comments/v1/",
        author="u", published_at=to_iso(NOW), captured_at=NOW.isoformat(),
        lang="en", media_type="text",
        raw_metrics={"likes": 1, "upvotes": 1, "comments": 0, "saves": 0},
        source_native={"post_type": "hosted:video", "content_url": "https://v.redd.it/abc"},
        tags=[], raw_snippet="",
    )
    with patch("pipeline.transcribe.transcribe_reddit_video",
               side_effect=AssertionError("should not be called when disabled")):
        failed = _enrich_top_with_transcripts([item], {"transcribe": {"enabled": False}})
    assert failed == 0
    print("✅ test_enrich_top_with_transcripts_can_be_disabled_via_cfg")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
