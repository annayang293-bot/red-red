"""主线编排(Step 4)—— 把各块串成一条端到端流水线。

流程(对齐 legacy main.py):
  抓取(多源)→ 打分 → 三闸门过滤 → 去重 → 打标签 → Top-N 选择(含 PH 配额)
  → AI 点评(强/中/弱)→ sanity 自检 → RunResult。

不连 Supabase(=Step 6):产出 RunResult 放内存(posts ~ posts_archive,top ~ report_top20)。
source / AI 都可注入,便于离线用 stub 跑通+测试(真实 Reddit/OpenAI 联调留 Step 6)。
"""
from __future__ import annotations

import collections
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import DEFAULT_CONFIG
from .scoring import score_items, filter_hot
from .merge import dedup_items, enrich_tags, select_ranked
from .ai_review import heuristic_review


def config_fingerprint(cfg: dict, keywords: list) -> str:
    """配置指纹(系统③ V2 校准分组用):cfg 关键段 + 词表的稳定 hash。"""
    payload = {
        "scoring": cfg.get("scoring"), "filter": cfg.get("filter"),
        "merge": cfg.get("merge"), "keywords": sorted(keywords),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "cfg_" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


@dataclass
class RunResult:
    topic: str
    run_at: str
    triggered_by: str
    status: str                       # completed | failed
    config_fingerprint: str
    candidates_count: int             # 抓取候选总数
    scored_count: int                 # 过三闸门 + 去重后(~ posts_archive 当次新增)
    posts: list                       # scored 集(HotItem)— → posts_archive
    top: list                         # [{rank, item, tier, comment}] — → report_top20
    ai_mode: str                      # ai | heuristic
    sanity: dict
    failed_sources: list = field(default_factory=list)

    @property
    def top_count(self) -> int:
        return len(self.top)


def sanity_check(report_items, ai_mode, failed_sources, ai_meta_missing=0):
    """跑完扫内容合理性(Anna 锁定 5 项的精简版,加固点 #4)。"""
    n = len(report_items)
    anomalies = []
    if n == 0:
        anomalies.append("empty_report(0条)")
        return {"status": "OK_WITH_ANOMALY", "anomalies": anomalies, "n": 0,
                "ai_mode": ai_mode, "source_dist": {}, "failed_sources": failed_sources}
    if n < 10:
        anomalies.append(f"item_count_low(n={n}<10)")
    if ai_mode != "ai":
        anomalies.append(f"ai_degraded(mode={ai_mode})")
    # AI 点评漏盖某些条(Step 6 真 LLM 解析故障可能触发)→ 标记,别让日报悄悄半残
    if ai_meta_missing:
        anomalies.append(f"ai_meta_missing({ai_meta_missing}/{n})")
    src_counts = collections.Counter(it.source for it in report_items)
    top_src, top_n = src_counts.most_common(1)[0]
    if top_n / n > 0.75:
        anomalies.append(f"source_skew({top_src}={top_n}/{n}>75%)")
    if failed_sources:
        anomalies.append(f"source_fetch_failed({','.join(failed_sources)})")
    status = "OK_WITH_ANOMALY" if anomalies else "OK"
    return {"status": status, "anomalies": anomalies, "n": n, "ai_mode": ai_mode,
            "source_dist": dict(src_counts), "failed_sources": failed_sources}


def run_pipeline(topic, sources, *, cfg=None, keywords=None,
                 review_fn=heuristic_review, triggered_by="manual", now=None):
    """跑一条主线。sources = 已构建的 Source 实例列表(测试传 stub,生产传真适配器)。"""
    cfg = cfg or DEFAULT_CONFIG
    keywords = keywords or cfg["keywords"]
    now = now or datetime.now(timezone.utc)
    fp = config_fingerprint(cfg, keywords)

    # ① 抓取(多源;单源失败不致命,记 failed)
    all_items, failed = [], []
    for src in sources:
        try:
            items = src.fetch()
        except Exception as e:  # noqa: BLE001 — 单源失败隔离,不拖垮整条线
            failed.append(getattr(src, "name", "unknown"))
            print(f"[runner] 源 {getattr(src,'name','?')} 抓取失败: {e}")
            continue
        # 适配器若暴露 failed_subs(Reddit 部分版块失败)也记一笔
        if getattr(src, "failed_subs", None):
            failed.append(f"{src.name}:{','.join(src.failed_subs)}")
        all_items.extend(items)
        for it in items:
            if it.source_native is None:
                it.source_native = {}
            it.source_native["config_fingerprint"] = fp

    # ②–③ 打分 + 三闸门
    score_items(all_items, cfg, keywords)
    hot = filter_hot(all_items, cfg)
    hot = dedup_items(hot, cfg)
    enrich_tags(hot, keywords, cfg)
    hot.sort(key=lambda x: x.hot_score, reverse=True)

    # ④ Top-N 选择(PH 配额 + 全局 hot 补满)
    thr = cfg["filter"]["relevance_threshold"]
    quota_srcs = set((cfg.get("merge", {}) or {}).get("source_quota", {}) or {})
    rel_pool = dedup_items(
        [it for it in all_items if it.source in quota_srcs and it.relevance_score >= thr],
        cfg)
    report_items = select_ranked(hot, cfg, cfg["output"]["daily_top_n"], quota_pool=rel_pool)
    enrich_tags(report_items, keywords, cfg)

    # ⑤ AI 点评(强/中/弱)
    meta, ai_mode = review_fn(report_items, cfg)
    top = [{"rank": i + 1, "item": it,
            "tier": (meta.get(it.id) or {}).get("tier"),
            "comment": (meta.get(it.id) or {}).get("comment"),
            "xhs_title": (meta.get(it.id) or {}).get("xhs_title")}
           for i, it in enumerate(report_items)]

    # ⑥ sanity
    ai_meta_missing = sum(1 for r in top if not r["tier"])
    sanity = sanity_check(report_items, ai_mode, failed, ai_meta_missing=ai_meta_missing)

    # 运行态:有源、全抓失败且零候选 = 上游全挂(failed);否则 completed(含"跑成功但空")。
    # (Step 6 接 DB/前端时再细化运行态语义。)
    status = "failed" if (sources and not all_items and failed) else "completed"

    return RunResult(
        topic=topic, run_at=now.isoformat(), triggered_by=triggered_by,
        status=status, config_fingerprint=fp,
        candidates_count=len(all_items), scored_count=len(hot),
        posts=hot, top=top, ai_mode=ai_mode, sanity=sanity, failed_sources=failed,
    )


def build_topic_sources(topic, mapper, cfg=None, *, reddit_cls=None, ph_cls=None,
                        base_reddit_cfg=None):
    """便利:主题映射 → 版块清单 → 构建 RedditSource(+PH)。供生产真实抓取用。

    (Step 4 的 stub 测试不走这条;真实 Reddit/OAuth 联调在 Step 6。)
    """
    cfg = cfg or DEFAULT_CONFIG
    mapping = mapper.map_topic(topic)
    subreddits = mapping.subreddit_names
    sources = []
    if reddit_cls is not None:
        # cfg 驱动 + base_reddit_cfg 显式覆盖;subreddits 来自映射
        rcfg = {**(cfg.get("reddit") or {}), **(base_reddit_cfg or {})}
        rcfg["subreddits"] = subreddits
        sources.append(reddit_cls({"reddit": rcfg}))
    if ph_cls is not None:
        # cfg 驱动:别硬编码 rss,否则真实配置切到 token 模式会被静默退回 RSS。
        # cfg 没给 product_hunt 段时,PH 源内部默认 rss。
        pcfg = dict(cfg.get("product_hunt") or {})
        sources.append(ph_cls({"product_hunt": pcfg}))
    return sources, mapping
