"""Step 4 主线 runner — 端到端测试(stub 源,不依赖网络/OpenAI)。

跑法: python3 system1-app/pipeline/tests/test_runner.py
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


# 18 条相关 AI 帖(每条含 ≥2 个关键词 → relevance 满分),互动按梯度递减;
# + 2 条无关高互动帖(rel=0,应被相关性闸门刷掉)。真实 pipeline N≈390,这里给够 N
# 让"Top 20%"闸门不退化(小 N + 单个爆款 outlier 会把闸门卡到只剩 1 条)。
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
    # 无关高互动(中等量级,不主导归一化;靠 relevance 闸门被刷)
    items.append(mk("reddit", "off1", "cooking pasta recipe at home", 700, 150, 1))
    items.append(mk("reddit", "off2", "my cat is so cute today", 650, 120, 1))
    return items


def _ph_items():
    # PH 零互动(likes/comments=0)→ hot 0,靠配额露出
    return [
        mk("product_hunt", "ph1", "Nota: AI Notes & Voice tool", 0, 0, 3),
        mk("product_hunt", "ph2", "AI Outreach SaaS launch", 0, 0, 6),
        mk("product_hunt", "ph3", "AI product roadmap app", 0, 0, 9),
    ]


def test_end_to_end_basic():
    reddit = StubSource("reddit", _reddit_items())
    ph = StubSource("product_hunt", _ph_items())
    res = run_pipeline("AI 创业", [reddit, ph], now=NOW)

    assert res.status == "completed"
    assert res.candidates_count == 23, res.candidates_count   # 20 reddit + 3 PH
    assert res.scored_count >= 3, res.scored_count
    assert res.top_count >= 4, res.top_count
    # 每条 top 都有 tier + comment + config_fingerprint
    for row in res.top:
        assert row["tier"] in ("强迁移", "中等迁移", "弱迁移"), row
        assert row["comment"]
        assert row["item"].source_native.get("config_fingerprint") == res.config_fingerprint
    # 无关帖(pasta/cat,rel 低)不应进 top
    assert all(
        "pasta" not in r["item"].title and "cat" not in r["item"].title
        for r in res.top
    )
    # 三档都应出现(top 足够大时)
    tiers = {r["tier"] for r in res.top}
    assert "强迁移" in tiers, tiers
    print(f"✅ test_end_to_end_basic (candidates={res.candidates_count} "
          f"scored={res.scored_count} top={res.top_count} tiers={tiers} ai={res.ai_mode})")


def test_ph_quota_surfaces_zero_engagement():
    """PH 零互动会被 hot 闸门刷掉,但靠配额(=2)在 top 里保底露出。"""
    reddit = StubSource("reddit", _reddit_items())
    ph = StubSource("product_hunt", _ph_items())
    res = run_pipeline("AI 创业", [reddit, ph], now=NOW)
    ph_in_top = [r for r in res.top if r["item"].source == "product_hunt"]
    assert 1 <= len(ph_in_top) <= 2, f"PH 配额应 ≤2 且有露出, 实际 {len(ph_in_top)}"
    print(f"✅ test_ph_quota_surfaces_zero_engagement (PH in top = {len(ph_in_top)})")


def test_failed_source_isolated():
    reddit = StubSource("reddit", _reddit_items())
    res = run_pipeline("AI 创业", [reddit, BoomSource()], now=NOW)
    assert res.status == "completed"            # 单源失败不拖垮
    assert "boom" in res.failed_sources, res.failed_sources
    assert any("source_fetch_failed" in a for a in res.sanity["anomalies"])
    assert res.top_count >= 1                    # reddit 仍出结果
    print("✅ test_failed_source_isolated")


def test_empty_sources_sane():
    res = run_pipeline("AI 创业", [StubSource("reddit", [])], now=NOW)
    assert res.status == "completed"
    assert res.candidates_count == 0
    assert res.top_count == 0
    assert any("empty_report" in a for a in res.sanity["anomalies"])
    print("✅ test_empty_sources_sane")


def test_fingerprint_stable():
    fp1 = config_fingerprint(DEFAULT_CONFIG, DEFAULT_CONFIG["keywords"])
    fp2 = config_fingerprint(DEFAULT_CONFIG, list(reversed(DEFAULT_CONFIG["keywords"])))
    assert fp1 == fp2, "词表顺序不应改变指纹(内部排序)"
    assert fp1.startswith("cfg_")
    print("✅ test_fingerprint_stable")


def test_relevance_gate_filters_offtopic():
    """全是无关帖 → relevance 全 0 → 过不了闸门 → 空报告。"""
    off = [mk("reddit", f"o{i}", "cooking pasta recipe", 500, 100, 2) for i in range(6)]
    res = run_pipeline("AI 创业", [StubSource("reddit", off)], now=NOW)
    assert res.top_count == 0, "无关内容不该进 top"
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
    """🔴 回归(Rex Step4):build_topic_sources 必须 cfg 驱动,不能硬编码 PH=rss。"""
    cfg = {**DEFAULT_CONFIG,
           "product_hunt": {"auth_mode": "token"},
           "reddit": {"auth_mode": "oauth"}}
    sources, mapping = build_topic_sources(
        "AI 创业", _FakeMapper(), cfg, reddit_cls=_CapReddit, ph_cls=_CapPH)
    ph = next(s for s in sources if s.name == "product_hunt")
    assert ph.cfg["product_hunt"]["auth_mode"] == "token", ph.cfg   # 不被 rss 覆盖
    rd = next(s for s in sources if s.name == "reddit")
    assert rd.cfg["reddit"]["auth_mode"] == "oauth"
    assert rd.cfg["reddit"]["subreddits"] == ["OpenAI", "startups"]
    print("✅ test_build_topic_sources_respects_cfg")


def test_all_sources_fail_status_failed():
    """全源失败 + 零候选 → status=failed(区分'跑成功但空')。"""
    res = run_pipeline("AI 创业", [BoomSource()], now=NOW)
    assert res.status == "failed", res.status
    assert "boom" in res.failed_sources
    print("✅ test_all_sources_fail_status_failed")


def test_ai_meta_missing_flagged():
    """🟡(Rex Step4):review_fn 漏盖某些 item → sanity 标 ai_meta_missing,不静默半残。"""
    reddit = StubSource("reddit", _reddit_items())
    res = run_pipeline("AI 创业", [reddit], now=NOW,
                       review_fn=lambda items, cfg: ({}, "ai"))  # 空 meta
    assert res.top_count >= 1
    assert any("ai_meta_missing" in a for a in res.sanity["anomalies"]), res.sanity["anomalies"]
    print("✅ test_ai_meta_missing_flagged")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
