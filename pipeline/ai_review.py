"""AI review + strong/medium/weak migration tier.

Interface: review_fn(items, cfg) -> (meta: dict[item_id -> {tier, comment}], mode: str)
  - mode = "ai" (real LLM) / "heuristic" (degraded / placeholder)
Two implementations:
  - heuristic_review: no OpenAI required; tiering by hot_score + template critique (works offline + in tests).
  - openai_review: real LLM (gpt-4o-mini, direct to api.openai.com, bypassing the Slock proxy).
    **Any failure → wholesale fallback to heuristic** (Step 6 deferred "LLM-all-fail → heuristic fallback").
select_review_fn(): if OPENAI_API_KEY is set → openai_review, otherwise heuristic_review.
"""
from __future__ import annotations

import json
import os


TIERS = ("强迁移", "中等迁移", "弱迁移")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"


def heuristic_review(items, cfg):
    """Placeholder: 3-tier split by hot_score's relative position in this report + template critique.

    Note: this isn't a real "migration potential" judgment (that requires an LLM to read the content);
    it just lets the engine run end-to-end offline. To switch to a real LLM, replace this function —
    the interface doesn't change.
    """
    meta = {}
    if not items:
        return meta, "heuristic"
    ranked = sorted(items, key=lambda x: x.hot_score, reverse=True)
    n = len(ranked)
    for idx, it in enumerate(ranked):
        frac = idx / n
        if frac < 0.34:
            tier, note = "强迁移", "互动高、话题性强,适合直接做选题。"
        elif frac < 0.74:
            tier, note = "中等迁移", "有看点,需结合人设加工。"
        else:
            tier, note = "弱迁移", "偏圈内/工具向,大众吸引力一般。"
        meta[it.id] = {"tier": tier, "comment": note}
    return meta, "heuristic"


def select_review_fn():
    """If OPENAI_API_KEY is set → real LLM review; otherwise heuristic (works offline / without a key)."""
    return openai_review if os.environ.get("OPENAI_API_KEY") else heuristic_review


def _openai_prompt(items) -> str:
    # Prompt body stays in Chinese: the model is asked to produce Chinese tier names + critique + xhs_title.
    # Comments enrichment (Anna 2026-05-31): when a post has community comments attached, include
    # the top 3 (by score) as additional context. Community responses often surface the real angle —
    # an OpenAI post with 1k upvotes but split-opinion comments is a different tier than one with
    # unanimous praise. Comments come from source_native["comments"] (set by runner.py's enrich step).
    lines = []
    for it in items:
        snippet = (it.raw_snippet or it.title or "")[:280]
        line = f"- id={it.id} | 标题={it.title!r} | 内容={snippet!r} | 来源={it.source}"
        sn = it.source_native or {}
        comments = sn.get("comments") or []
        if comments:
            top3 = comments[:3]
            # Compact each comment to ≤120 chars for the prompt (we don't need the full 800).
            comment_strs = [f"({c.get('score', 0)}赞) {c.get('body', '')[:120]}" for c in top3]
            line += " | 热评=" + " ｜ ".join(comment_strs)
        lines.append(line)
    return (
        "你在帮一个小红书博主筛选海外热点做选题。对每条内容(部分带 top 3 热评作为社区上下文):\n"
        "1) 判断'迁移到小红书做选题'的潜力:强迁移=直接能做;中等迁移=要加工/看人设;弱迁移=开发圈内/暂不建议。\n"
        "   **有热评的可以参考社区反应** —— 比如标题平淡但评论很激烈 → 可能是好选题;标题响亮但评论唱反调 → 谨慎。\n"
        "2) 给一句中文点评(为什么适合/不适合做小红书选题,≤40字)。\n"
        "3) 起一个中文小红书标题 xhs_title(口语化、有钩子,≤20字)。\n"
        '只输出 JSON:{"items":[{"id":"...","tier":"强迁移|中等迁移|弱迁移","comment":"...","xhs_title":"..."}]}。\n\n'
        + "\n".join(lines)
    )


def openai_review(items, cfg):
    """Real LLM review (direct to api.openai.com). Any failure (no key / network / parse) → wholesale fallback to heuristic.

    Partial coverage (the LLM only reviewed some items) is flagged by the runner's ai_meta_missing
    sanity check; it is not back-filled here.
    """
    if not items:
        return {}, "heuristic"
    try:
        import requests  # Lazy import: the heuristic path doesn't need requests.
        key = os.environ["OPENAI_API_KEY"]  # Missing → KeyError → fallback
        # Direct to api.openai.com: trust_env=False ignores environment proxies (Slock proxy 401s on OpenAI).
        sess = requests.Session()
        sess.trust_env = False
        resp = sess.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_MODEL", OPENAI_MODEL),
                "messages": [{"role": "user", "content": _openai_prompt(items)}],
                "response_format": {"type": "json_object"},
                "temperature": 0.4,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        valid = set(TIERS)
        meta = {}
        for row in parsed.get("items", []):
            iid, tier = row.get("id"), row.get("tier")
            if iid and tier in valid:
                meta[iid] = {"tier": tier,
                             "comment": (row.get("comment") or "").strip(),
                             "xhs_title": (row.get("xhs_title") or "").strip() or None}
        if not meta:
            raise ValueError("LLM response had no valid review entries")
        return meta, "ai"
    except Exception as e:  # noqa: BLE001 — LLM all-fail isn't fatal; fall back to heuristic so the daily report isn't empty.
        print(f"[ai_review] LLM review failed, wholesale fallback to heuristic: {e}")
        return heuristic_review(items, cfg)
