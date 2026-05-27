"""主题 → reddit 版块 + 相关关键词 的 LLM 落地。**补完 Step 3 算法→真实抓取的接入**。

Step 3 的 TopicMapper 算法本身已 Rex 过审;它依赖"去 Reddit 搜版块"那条 gated 在 §7。
这里**绕开 Reddit 搜索**,只用 LLM(gpt-4o-mini)推荐版块 + per-topic 关键词,接进 run_once。
失败/无 key → 各自回退默认。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .topic_mapping import TopicMapper

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


def _openai_json(prompt: str) -> Optional[dict]:
    """调 LLM 拿 JSON。无 key / 任何失败 → None(调用方回退)。直连 api.openai.com,绕环境代理。"""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import requests
        sess = requests.Session()
        sess.trust_env = False
        r = sess.post(
            OPENAI_URL,
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            json={
                "model": os.environ.get("OPENAI_MODEL", DEFAULT_MODEL),
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.3,
            },
            timeout=60,
        )
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] LLM 调用失败: {e}", file=sys.stderr)
        return None


def _llm_subreddits(keyword: str) -> list[str]:
    """LLM 给该主题的相关 subreddit 名(供 TopicMapper 的 llm_suggest_fn)。要 10 个 → 后面过验证再砍。"""
    prompt = (
        f"主题:'{keyword}'。请推荐 10 个**真实存在、活跃、聚焦该主题**的英文 subreddit 名"
        "(不带 r/ 前缀;只用字母/数字/下划线;不要广义大众版块如 funny/news/pics)。\n"
        "**重要:只给真实存在的版块**——宁可少给也别瞎编(瞎编的会被验证步骤剔除,浪费名额)。\n"
        '只输出 JSON:{"subreddits":["name1","name2",...]}'
    )
    out = _openai_json(prompt) or {}
    return [
        s.strip().lstrip("r/").lstrip("/")
        for s in (out.get("subreddits") or [])
        if isinstance(s, str) and s.strip()
    ]


def _verify_subreddit(name: str) -> bool:
    """ping `r/<name>/about.json` 验证存在。404=确认不存在(LLM 幻觉)→剔除;
    其它结果(200/403/5xx/超时)= 不能确认不存在 → 保留(留给主抓取再试)。"""
    try:
        import requests
        ua = os.environ.get("REDDIT_USER_AGENT", "python:system1-app:v0.1 (by /u/system1app)")
        r = requests.get(
            f"https://www.reddit.com/r/{name}/about.json",
            headers={"User-Agent": ua}, timeout=10,
        )
        if r.status_code == 404:
            return False
        if r.status_code == 200:
            data = r.json().get("data", {}) or {}
            if data.get("subreddit_type") == "banned":
                return False
        return True
    except Exception:
        return True   # 验证不通也别拦——后面主抓取真碰到再算失败


def _noop_reddit_search(_keyword: str, _limit: int) -> list[dict]:
    """绕开 Reddit 版块搜索(匿名不稳)——返回空,候选全靠 LLM。"""
    return []


def resolve_topic(
    keyword: str,
    fallback_subreddits: list[str],
    fallback_keywords: list[str],
    *,
    target_count: int = 6,
) -> dict:
    """主题 → {subreddits: [...], keywords: [...]}。

    subreddits:复用 Step 3 TopicMapper(LLM suggest 路径,no-op reddit-search 绕开 §7 依赖)。
    keywords:per-topic 相关性判断词,单独一次 LLM 调用。两条任一失败,各自回退默认。
    """
    # 1) subreddits:LLM 推荐 → 验证存在(剔 LLM 幻觉的 404)→ 取 target_count 个;实在没了再回退
    raw_subs: list[str] = []
    try:
        # 让 LLM 路径多给(_llm_subreddits 要 10),TopicMapper 先按算法取 2*target;后面验证再砍
        mapper = TopicMapper(
            reddit_search_fn=_noop_reddit_search,
            llm_suggest_fn=_llm_subreddits,
            target_count=target_count * 2,
        )
        result = mapper.map_topic(keyword)
        raw_subs = list(result.subreddit_names)
    except Exception as e:  # noqa: BLE001
        print(f"[topic_resolve] TopicMapper 失败: {e}", file=sys.stderr)

    verified: list[str] = []
    for s in raw_subs:
        if _verify_subreddit(s):
            verified.append(s)
            if len(verified) >= target_count:
                break
        else:
            print(f"[topic_resolve] 剔除幻觉/无效版块:r/{s}", file=sys.stderr)
    used_llm_subs = bool(verified)
    if verified:
        subs = verified
    else:
        # LLM 整组都不可用 → 回退默认(虽然主题不匹配,至少有内容,不要空跑)
        print("[topic_resolve] 验证后无可用版块,回退默认", file=sys.stderr)
        subs = list(fallback_subreddits)

    # 2) keywords:per-topic 英文相关性词(Reddit 是英文站)
    kw_prompt = (
        f"主题:'{keyword}'。请给 15-20 个英文关键词,用于判断 Reddit 帖子是否相关该主题"
        "(词要具体、判别力强;不要 a/the/is 这种泛词;主题相关的实体/动作/概念都可)。\n"
        '只输出 JSON:{"keywords":["..."]}'
    )
    out = _openai_json(kw_prompt) or {}
    kws_raw = [
        k.strip().lower()
        for k in (out.get("keywords") or [])
        if isinstance(k, str) and k.strip()
    ]
    seen, kws = set(), []
    for k in kws_raw:
        if k not in seen:
            seen.add(k)
            kws.append(k)
    used_llm_kws = bool(kws)
    if not kws:
        kws = list(fallback_keywords)

    # **分别**标来源(Rex 🔴):决定要不要松闸**只看版块来源**,跟关键词来源解耦——
    # 否则"LLM 版块成功 + 关键词回退默认 AI 词表"会被误标 fallback,导致非 AI 主题
    # 退回严闸 + AI 词,把正常帖错杀。
    return {
        "subreddits": subs,
        "keywords": kws,
        "subreddits_source": "llm" if used_llm_subs else "fallback",
        "keywords_source": "llm" if used_llm_kws else "fallback",
    }
