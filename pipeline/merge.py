"""去重 / 自动标签 / 配额选择 —— 从 legacy 移植(post-scoring 编排,不改打分)。

- dedup_items: 跨源按 dedup_key(规范化 URL)合并,保留 hot_score 最高;平局按源优先级。
- enrich_tags: 原生标签 + 命中的 relevance 关键词,去重,上限 max_tags。
- select_ranked: 来源配额(如 PH=2,靠配额露出,配额内按 recency)+ 全局 hot 补满。
"""
from __future__ import annotations

from .scoring import _kw_hit


def _mcfg(cfg: dict) -> dict:
    return cfg.get("merge", {}) or {}


def dedup_items(items, cfg):
    if not _mcfg(cfg).get("dedup", True):
        return items
    prio = _mcfg(cfg).get("dedup_source_priority", ["reddit", "product_hunt"])

    def rank(src):
        return prio.index(src) if src in prio else len(prio)

    best: dict = {}
    order: list = []
    for it in items:
        k = it.dedup_key
        if not k:
            order.append(it)
            continue
        cur = best.get(k)
        if cur is None:
            best[k] = it
            order.append(it)
        else:
            if (it.hot_score > cur.hot_score) or (
                it.hot_score == cur.hot_score and rank(it.source) < rank(cur.source)
            ):
                order[order.index(cur)] = it
                best[k] = it
    return order


def enrich_tags(items, keywords, cfg):
    mc = _mcfg(cfg)
    if not mc.get("tag_with_keywords", True):
        return items
    max_tags = int(mc.get("max_tags", 8))
    prefix = mc.get("tag_prefix", True)
    for it in items:
        text = f"{it.title} {it.raw_snippet or ''}".lower()
        native = it.tags or []
        kw_hits = [k for k in keywords if _kw_hit(k, text)]
        if prefix:
            native = [t if str(t).startswith(("source:", "kw:")) else f"source:{t}"
                      for t in native]
            kw_hits = [f"kw:{k}" for k in kw_hits]
        merged = list(dict.fromkeys([*native, *kw_hits]))
        it.tags = merged[:max_tags]
    return items


def _recency_key(it):
    return it.published_at or it.captured_at or ""


def select_ranked(items, cfg, limit, quota_pool=None):
    """展示层 Top-N 选择:来源配额优先(零互动源靠 quota 保底露出,按 recency)+ 全局 hot 补满。"""
    base = sorted(items, key=lambda x: x.hot_score, reverse=True)
    quotas = _mcfg(cfg).get("source_quota", {}) or {}
    extra = quota_pool or []
    picked = []
    picked_ids = set()
    for src, q in quotas.items():
        cand = [x for x in base if x.source == src]
        seen = {x.id for x in cand}
        cand += [x for x in extra if x.source == src and x.id not in seen]
        cand.sort(key=_recency_key, reverse=True)
        for x in cand[: int(q)]:
            if x.id not in picked_ids and len(picked) < limit:
                picked.append(x)
                picked_ids.add(x.id)
    for x in base:
        if len(picked) >= limit:
            break
        if x.id not in picked_ids:
            picked.append(x)
            picked_ids.add(x.id)
    picked.sort(key=lambda x: x.hot_score, reverse=True)
    return picked[:limit]
