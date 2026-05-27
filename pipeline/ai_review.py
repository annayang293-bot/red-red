"""AI 点评 + 强/中/弱迁移分档。

接口:review_fn(items, cfg) -> (meta: dict[item_id -> {tier, comment}], mode: str)
  - mode = "ai"(真 LLM)/ "heuristic"(降级/占位)
两种实现:
  - heuristic_review:无需 OpenAI,按 hot_score 分档 + 模板点评(离线跑通 + 测试)。
  - openai_review:真 LLM(gpt-4o-mini,走 api.openai.com 直连,绕开 Slock 代理)。
    **任何失败 → 整体回退 heuristic**(Step 6 deferred「LLM 全失败回退 heuristic」)。
select_review_fn():有 OPENAI_API_KEY → openai_review,否则 heuristic_review。
"""
from __future__ import annotations

import json
import os


TIERS = ("强迁移", "中等迁移", "弱迁移")

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODEL = "gpt-4o-mini"


def heuristic_review(items, cfg):
    """占位版:按 hot_score 在本次报告内的相对位置分三档 + 模板点评。

    注意:这不是真"迁移潜力"判断(那需要 LLM 读内容),只是让引擎能离线跑通。
    真 LLM 版替换这个函数即可,接口不变。
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
    """有 OPENAI_API_KEY → 真 LLM 点评;否则 heuristic(离线/无 key 也能跑)。"""
    return openai_review if os.environ.get("OPENAI_API_KEY") else heuristic_review


def _openai_prompt(items) -> str:
    lines = []
    for it in items:
        snippet = (it.raw_snippet or it.title or "")[:280]
        lines.append(f"- id={it.id} | 标题={it.title!r} | 内容={snippet!r} | 来源={it.source}")
    return (
        "你在帮一个小红书博主筛选海外热点做选题。对每条内容:\n"
        "1) 判断'迁移到小红书做选题'的潜力:强迁移=直接能做;中等迁移=要加工/看人设;弱迁移=开发圈内/暂不建议。\n"
        "2) 给一句中文点评(为什么适合/不适合做小红书选题,≤40字)。\n"
        "3) 起一个中文小红书标题 xhs_title(口语化、有钩子,≤20字)。\n"
        '只输出 JSON:{"items":[{"id":"...","tier":"强迁移|中等迁移|弱迁移","comment":"...","xhs_title":"..."}]}。\n\n'
        + "\n".join(lines)
    )


def openai_review(items, cfg):
    """真 LLM 点评(api.openai.com 直连)。任何失败(无 key/网络/解析)→ 整体回退 heuristic。

    部分覆盖(LLM 只点评了一部分条目)由 runner 的 sanity ai_meta_missing 标记,不在这里补。
    """
    if not items:
        return {}, "heuristic"
    try:
        import requests  # 延迟导入:heuristic 路径不依赖 requests
        key = os.environ["OPENAI_API_KEY"]  # 缺 → KeyError → 回退
        # 直连 api.openai.com:trust_env=False 忽略环境代理(Slock 代理对 OpenAI 会 401)。
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
            raise ValueError("LLM 返回未解析出任何有效点评")
        return meta, "ai"
    except Exception as e:  # noqa: BLE001 — LLM 全失败不致命,降级 heuristic 保证日报不空
        print(f"[ai_review] LLM 点评失败,整体回退 heuristic: {e}")
        return heuristic_review(items, cfg)
