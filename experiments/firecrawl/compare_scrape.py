"""Firecrawl 对比测试 —— 现状抓取 vs Firecrawl 全文+评论。

用途:验证 Firecrawl 对系统①/②的增量价值。拿一条今天日报里的热帖,
对比「我们现在存的(title + ≤500字摘要,无评论)」vs「Firecrawl 抓的(整页 markdown,
含正文全文 + 楼下高赞评论)」。

跑法:
    export FIRECRAWL_API_KEY="fc-..."   # Anna 提供,只放环境变量
    python3 compare_scrape.py
"""
from __future__ import annotations

import json
import os
import sys

# 今天日报 Top 强迁移帖:r/OpenAI「DeepSeek 戳破美国 AI 泡沫」(👍198 💬61)
TARGET_URL = "https://www.reddit.com/r/OpenAI/comments/1tm49d0/deepseek_just_popped_the_american_ai_bubble/"
HOTSPOTS = "/Users/annayang/Projects/xhs-ai-ip/system1-scraper/data/daily_hotspots.json"


def show_baseline():
    """我们现在的抓取存了什么(从 daily_hotspots.json 找这条)。"""
    print("=" * 70)
    print("【现状】我们现在 pipeline 存的内容")
    print("=" * 70)
    try:
        data = json.load(open(HOTSPOTS))
        items = data if isinstance(data, list) else data.get("items", data.get("posts", []))
        hit = None
        for it in items:
            u = (it.get("url") or "") + (it.get("dedup_key") or "")
            if "1tm49d0" in u or "deepseek_just_popped" in u.lower():
                hit = it
                break
        if not hit:
            print("(没在 daily_hotspots.json 找到这条,改打印第一条作示例)")
            hit = items[0] if items else {}
        print("title      :", hit.get("title"))
        print("url        :", hit.get("url"))
        print("raw_metrics:", hit.get("raw_metrics"))
        snip = hit.get("raw_snippet") or ""
        print("raw_snippet:", repr(snip[:600]), f"(len={len(snip)})")
        print("comments   : <无 —— 我们现在不抓评论>")
    except Exception as e:
        print("读 baseline 失败:", e)


def show_firecrawl():
    print()
    print("=" * 70)
    print("【Firecrawl】整页 scrape → markdown(正文全文 + 评论)")
    print("=" * 70)
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        print("✗ FIRECRAWL_API_KEY 未设置 —— 先 export 再跑。")
        return
    md = _scrape_markdown(TARGET_URL, key)
    if md is None:
        return
    print(f"markdown 总长度: {len(md)} 字符")
    # 粗略数一下评论密度(reddit markdown 里楼层/赞通常含这些信号)
    low = md.lower()
    print("含 'comment' 次数 :", low.count("comment"))
    print("含 'reply' 次数   :", low.count("reply"))
    print("含 'points'/'upvote':", low.count("point") + low.count("upvote"))
    print()
    print("---- 前 2000 字预览 ----")
    print(md[:2000])
    print("---- 末 1200 字预览(通常是评论区)----")
    print(md[-1200:])
    # 存全量供细看
    out = os.path.join(os.path.dirname(__file__), "firecrawl_out.md")
    open(out, "w").write(md)
    print(f"\n(全文已存 {out})")


def _scrape_markdown(url: str, key: str):
    """兼容新旧 firecrawl-py API,返回 markdown 文本。"""
    try:
        from firecrawl import Firecrawl  # 新版 SDK
        app = Firecrawl(api_key=key)
        doc = app.scrape(url, formats=["markdown"])
        # 新版返回对象或 dict
        md = getattr(doc, "markdown", None)
        if md is None and isinstance(doc, dict):
            md = doc.get("markdown") or (doc.get("data") or {}).get("markdown")
        return md
    except ImportError:
        pass
    except Exception as e:
        print("新版 API 失败,尝试旧版:", e)
    try:
        from firecrawl import FirecrawlApp  # 旧版 SDK
        app = FirecrawlApp(api_key=key)
        res = app.scrape_url(url, params={"formats": ["markdown"]})
        if isinstance(res, dict):
            return res.get("markdown") or (res.get("data") or {}).get("markdown")
        return getattr(res, "markdown", None)
    except Exception as e:
        print("✗ Firecrawl scrape 失败:", e)
        return None


if __name__ == "__main__":
    show_baseline()
    show_firecrawl()
