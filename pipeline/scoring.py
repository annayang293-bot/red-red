"""打分 + 相关性 + 三闸门过滤 —— 从 legacy 移植(M1 FROZEN 公式,不改)。

hot_score = (w_like*likes + w_comment*comments + w_saveshare*saves) * time_decay
time_decay = 0.5 ^ (age_hours / half_life_hours) → 按来源内分布归一化到 0–100
relevance_score = 关键词词表命中度(0–1)
热点 = relevance >= 阈值 且 hot_score 进 Top hot_top_percent% 且 >= 绝对地板
作用于 HotItem(字段:raw_metrics / published_at / captured_at / title / raw_snippet /
hot_score / relevance_score)。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone


def _age_hours(published_iso, captured_iso: str) -> float:
    cap = datetime.fromisoformat(captured_iso)
    if not published_iso:
        return 0.0
    pub = datetime.fromisoformat(published_iso)
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if cap.tzinfo is None:
        cap = cap.replace(tzinfo=timezone.utc)
    return max(0.0, (cap - pub).total_seconds() / 3600.0)


def raw_hot(metrics: dict, sw: dict, age_h: float, half_life: float) -> float:
    likes = metrics.get("likes", 0) or 0
    comments = metrics.get("comments", 0) or 0
    saves = metrics.get("saves", 0) or 0
    base = sw["w_like"] * likes + sw["w_comment"] * comments + sw["w_saveshare"] * saves
    decay = 0.5 ** (age_h / half_life) if half_life > 0 else 1.0
    return base * decay


def _kw_hit(kw: str, text: str) -> bool:
    """ASCII 关键词用词边界(避免 ai∈said / ml∈html);CJK 关键词保留子串匹配。"""
    k = kw.lower().strip()
    if not k:
        return False
    if re.fullmatch(r"[a-z0-9 +.&/_-]+", k):
        return re.search(rf"\b{re.escape(k)}\b", text) is not None
    return k in text


def relevance(title: str, snippet: str, keywords, full_hit: float = 5.0) -> float:
    text = f"{title} {snippet or ''}".lower()
    if not keywords or full_hit <= 0:
        return 0.0
    hits = sum(1 for kw in keywords if _kw_hit(kw, text))
    return min(1.0, hits / float(full_hit))


def score_items(items, cfg, keywords):
    """就地写入 hot_score(归一化 0–100) / relevance_score。返回 items。"""
    sw = cfg["scoring"]
    half_life = sw["half_life_hours"]
    full_hit = cfg.get("filter", {}).get("relevance_full_hit", 5)
    raws = []
    for it in items:
        age_h = _age_hours(it.published_at, it.captured_at)
        r = raw_hot(it.raw_metrics, sw, age_h, half_life)
        raws.append(r)
        it.relevance_score = round(relevance(it.title, it.raw_snippet, keywords, full_hit), 4)
    max_raw = max(raws) if raws else 0.0
    for it, r in zip(items, raws):
        it.hot_score = round((r / max_raw * 100.0) if max_raw > 0 else 0.0, 2)
    return items


def filter_hot(items, cfg):
    """三闸门:相关 + 相对热度(Top X%)+ 绝对热度地板。"""
    if not items:
        return []
    thr = cfg["filter"]["relevance_threshold"]
    top_pct = cfg["filter"]["hot_top_percent"]
    floor = cfg["filter"].get("min_absolute_hot_score", 0.0)
    scores = sorted((it.hot_score for it in items), reverse=True)
    cutoff_idx = max(0, int(len(scores) * top_pct / 100.0) - 1)
    hot_cutoff = scores[cutoff_idx] if scores else 0.0
    return [
        it for it in items
        if it.relevance_score >= thr
        and it.hot_score >= hot_cutoff
        and it.hot_score >= floor
    ]
