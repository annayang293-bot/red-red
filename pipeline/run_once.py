"""单次跑:抓 → 打分 → 三闸门 → 去重 → 标签 → Top-N → AI 点评 → 存 Supabase。

供 Node `POST /api/run` 以子进程方式调用(单次 30–60s,Vercel Fluid 300s 够)。

用法: python -m pipeline.run_once "AI 创业" [--triggered-by manual|cron]
约定: **stdout 只打印一行结果 JSON**(供 Node 解析);pipeline/adapter 的日志全部走 stderr。
  成功: {"ok":true,"run_id":N,"topic":...,"status":...,"ai_mode":...,"posts":M,"top":K,
         "failed_sources":[...],"sanity_status":...}
  失败: {"ok":false,"error":"..."} + 退出码 1。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import sys

from .ai_review import select_review_fn
from .config import DEFAULT_CONFIG
from .runner import run_pipeline
from .sources.product_hunt_source import ProductHuntSource
from .sources.reddit_source import RedditSource
from .store import SupabaseStore
from .supa import _load_dotenv_if_present, get_client
from .topic_resolve import resolve_topic

# AI 创业相关的默认版块——LLM 主题映射失败时回退用。
DEFAULT_SUBREDDITS = ["OpenAI", "SaaS", "Entrepreneur", "startups", "artificial", "indiehackers"]


def build_sources(cfg: dict, subreddits: list[str]) -> list:
    """构建真实数据源(Reddit + PH)。subreddits 由 resolve_topic 给(LLM 按主题推荐;失败回退默认)。"""
    reddit_cfg = dict(cfg.get("reddit") or {})
    reddit_cfg.setdefault("auth_mode", "public")
    reddit_cfg["subreddits"] = subreddits
    return [
        RedditSource({"reddit": reddit_cfg}),
        ProductHuntSource({"product_hunt": dict(cfg.get("product_hunt") or {})}),
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跑一次主线并落库 Supabase")
    ap.add_argument("topic", help="主题词,如 'AI 创业'")
    ap.add_argument("--triggered-by", default="manual", choices=["manual", "cron"])
    args = ap.parse_args(argv)

    cfg = DEFAULT_CONFIG
    # 先加载 .env(Reddit UA / OpenAI key 等 adapter 在 run_pipeline 期间就要读;
    # get_client 也会加载,但那在 pipeline 之后,太晚)。
    _load_dotenv_if_present()
    try:
        # pipeline / adapter 内部有 print 到 stdout 的日志 → 重定向到 stderr,
        # 保证真 stdout 只有最后一行结果 JSON(Node 才好解析)。
        with contextlib.redirect_stdout(sys.stderr):
            # 主题 → 该抓哪些 subreddits + per-topic 相关性词(LLM 自动算;失败各自回退默认)
            mapping = resolve_topic(args.topic, DEFAULT_SUBREDDITS, cfg["keywords"])
            print(f"[run_once] 主题映射 subs={mapping['subreddits_source']}/"
                  f"kws={mapping['keywords_source']} "
                  f"| subreddits={mapping['subreddits']} "
                  f"| keywords({len(mapping['keywords'])})={mapping['keywords'][:6]}…")
            # **只看版块来源**决定要不要松闸(Rex 🔴):LLM 选的版块本身就是话题过滤,
            # 关键词闸放宽(否则"Taylor Swift"这种正经名人帖会因标题没"celebrity"字面被错杀)。
            # 关键词回退到默认 AI 词表 ≠ 版块回退——这种情况仍应松闸,跟关键词来源解耦。
            run_cfg = cfg
            if mapping["subreddits_source"] == "llm":
                run_cfg = {**cfg, "filter": {**cfg["filter"], "relevance_threshold": 0.0}}
            sources = build_sources(run_cfg, mapping["subreddits"])
            res = run_pipeline(
                args.topic, sources, cfg=run_cfg, keywords=mapping["keywords"],
                review_fn=select_review_fn(), triggered_by=args.triggered_by,
            )
            run_id = SupabaseStore(get_client()).save(res)
        out = {
            "ok": True, "run_id": run_id, "topic": res.topic,
            "status": res.status, "ai_mode": res.ai_mode,
            "posts": res.scored_count, "top": res.top_count,
            "failed_sources": res.failed_sources,
            "sanity_status": res.sanity.get("status"),
            "subreddits": mapping["subreddits"],
            "keywords_count": len(mapping["keywords"]),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — 把失败收口成 JSON 给 Node,别让栈直接喷到 stdout
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
