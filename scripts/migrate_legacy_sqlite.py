"""一次性迁移:把 legacy system1-scraper/data/system1.db 的 hotspots 搬进 Supabase。

策略:按 DATE(captured_at) 分天 → 每天一条 runs(triggered_by=cron, person=legacy_import) →
每条 hotspot 进 posts_archive(append-only,native_id 从 URL 抽);每天 hot_score 前 20 进
report_top20,tier 按 heuristic 分位(top 34%=强 / 中 40% / 末 26%=弱)。

保守(Rex 数据层精神):
- posts_archive 用 (source, source_native_id) UNIQUE 防重 —— 先查已存在,只 insert 新的。
- 失败/不可识别原生 ID 的行(无法从 url 提取)→ 跳过 + 记 skipped。
- legacy SQLite 没存 AI 点评 → ai_review/comment/xhs_title 全 NULL,ai_mode='heuristic'。

跑法: python3 system1-app/scripts/migrate_legacy_sqlite.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # ~/Projects/xhs-ai-ip
sys.path.insert(0, str(REPO / "system1-app"))

from pipeline.supa import get_client  # noqa: E402

LEGACY_DB = REPO / "system1-scraper" / "data" / "system1.db"
TIER_DB = {"强": "强", "中": "中", "弱": "弱"}   # report_top20.tier CHECK 短名

# Reddit url → 帖子 native id 提取(/comments/<id>/...)
_REDDIT_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.I)


def extract_native_id(source: str, url: str | None, source_native: dict | None) -> str | None:
    """从 url 或 source_native 抽 native_id。Reddit 走 /comments/<id>/。"""
    if not url:
        return None
    if source == "reddit":
        m = _REDDIT_ID_RE.search(url)
        if m:
            return m.group(1)
        # 兜底:source_native.permalink
        perm = (source_native or {}).get("permalink") or ""
        m = _REDDIT_ID_RE.search(perm)
        return m.group(1) if m else None
    if source == "product_hunt":
        # PH url 模式 /products/<slug>/ 或 /posts/<slug>;legacy SQLite 实际无 PH,留兜底
        for prefix in ("/products/", "/posts/"):
            i = url.find(prefix)
            if i >= 0:
                rest = url[i + len(prefix):].split("/")[0].split("?")[0].strip()
                if rest:
                    return rest
        return None
    return None


def heuristic_tier_short(rank_in_day: int, n_in_day: int) -> str:
    """heuristic_review 同样的分位逻辑,返回 report_top20 的短名 (强/中/弱)。"""
    frac = rank_in_day / max(n_in_day, 1)
    if frac < 0.34:
        return "强"
    if frac < 0.74:
        return "中"
    return "弱"


def _parse_json(s):
    try:
        return json.loads(s) if s else None
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划,不写库")
    args = ap.parse_args(argv)

    if not LEGACY_DB.exists():
        print(f"❌ legacy DB 不存在: {LEGACY_DB}", file=sys.stderr)
        return 1

    # ---- 读 legacy ----
    conn = sqlite3.connect(str(LEGACY_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM hotspots ORDER BY captured_at").fetchall()
    print(f"legacy: {len(rows)} hotspots")

    # 按 day(YYYY-MM-DD) 分组
    by_day: dict[str, list] = defaultdict(list)
    skipped_no_id = 0
    for r in rows:
        day = (r["captured_at"] or "")[:10]
        sn = _parse_json(r["source_native"]) or {}
        native_id = extract_native_id(r["source"], r["url"], sn)
        if not native_id:
            skipped_no_id += 1
            continue
        by_day[day].append({"row": r, "native_id": native_id, "source_native": sn,
                            "raw_metrics": _parse_json(r["raw_metrics"]),
                            "tags": _parse_json(r["tags"])})
    if skipped_no_id:
        print(f"⚠️ {skipped_no_id} 行没能抽出 native_id(跳过)")
    days = sorted(by_day.keys())
    print(f"分组到 {len(days)} 天: {days[0]} → {days[-1]}")
    for d in days:
        print(f"  {d}: {len(by_day[d])} posts")

    if args.dry_run:
        print("\n--dry-run,不写库,退出")
        return 0

    # ---- 连 Supabase ----
    c = get_client()
    # legacy 数据归属 "AI 创业" 主题(legacy 一直在跑这个);它现在可能 active 也可能 archived 都行
    target = c.table("topics").select("topic_id,status").eq("keyword", "AI 创业") \
              .order("started_at", desc=True).limit(1).execute().data
    if not target:
        print("❌ 找不到 keyword='AI 创业' 的 topic(需要先在 UI 里跑过或建过)", file=sys.stderr)
        return 1
    topic_id = target[0]["topic_id"]
    print(f"legacy 数据归属 topic 'AI 创业' (topic_id={topic_id},status={target[0]['status']})")

    # 既有 posts(避免重插):按本次要写的 native_id 集合查
    all_nids = [p["native_id"] for d in days for p in by_day[d]]
    existing: dict[tuple, int] = {}
    BATCH = 200
    for i in range(0, len(all_nids), BATCH):
        chunk = all_nids[i:i + BATCH]
        ex = c.table("posts_archive").select("post_id,source,source_native_id") \
              .in_("source_native_id", chunk).execute().data
        for r in ex:
            existing[(r["source"], r["source_native_id"])] = r["post_id"]
    print(f"已在库的相关 posts: {len(existing)}(insert 时跳过这些)")

    # ---- 按天导入 ----
    total_runs = total_new_posts = total_report = 0
    for day in days:
        posts = by_day[day]
        # 当天按 hot_score 降序
        posts.sort(key=lambda p: float(p["row"]["hot_score"] or 0), reverse=True)
        n = len(posts)
        # 用当天最早 captured_at 当 started_at(否则 06:00 LA 也行)
        started_at = posts[-1]["row"]["captured_at"] or f"{day}T13:00:00+00:00"  # 06:00 LA = 13:00 UTC

        run_row = {
            "topic_id": topic_id, "topic_keyword": "AI 创业",
            "triggered_by": "cron", "triggered_by_person": "legacy_import",
            "status": "completed",
            "started_at": started_at, "finished_at": started_at,
            "posts_count": n, "top20_count": min(20, n),
            "ai_mode": "heuristic",                 # legacy 未存 AI per-post
            "sanity_status": "OK",
            "config_fingerprint": f"legacy_import_{day}",
        }
        run_id = c.table("runs").insert(run_row).execute().data[0]["run_id"]
        total_runs += 1

        # 准备本天的 posts_archive 插入(只插新的)
        key_to_pid: dict[tuple, int] = {}
        new_rows = []
        for p in posts:
            r = p["row"]
            key = (r["source"], p["native_id"])
            pid = existing.get(key)
            if pid is not None:
                key_to_pid[key] = pid
                continue
            new_rows.append({
                "source": r["source"], "source_native_id": p["native_id"],
                "title": r["title"], "url": r["url"],
                "raw_snippet": r["raw_snippet"],
                "raw_metrics": p["raw_metrics"],
                "hot_score": r["hot_score"], "relevance_score": r["relevance_score"],
                "tags_json": p["tags"], "ai_review": None,
                "published_at": r["published_at"], "fetched_at": r["captured_at"],
                "config_fingerprint": (p["source_native"] or {}).get("config_fingerprint") or f"legacy_import_{day}",
                "source_native": p["source_native"], "full_content_url": None,
                "run_id": run_id,
            })
        if new_rows:
            saved = c.table("posts_archive").insert(new_rows).execute().data
            for s in saved:
                k = (s["source"], s["source_native_id"])
                key_to_pid[k] = s["post_id"]
                existing[k] = s["post_id"]
            total_new_posts += len(new_rows)

        # report_top20:取当天 top 20(按 hot_score 已降序)
        top = posts[:20]
        rep_rows = []
        for i, p in enumerate(top, start=1):
            key = (p["row"]["source"], p["native_id"])
            pid = key_to_pid.get(key)
            if pid is None:
                continue   # 防御性
            rep_rows.append({
                "run_id": run_id, "post_id": pid, "rank": i,
                "tier": TIER_DB[heuristic_tier_short(i - 1, len(top))],
                "comment": None, "xhs_title": None,
            })
        if rep_rows:
            c.table("report_top20").insert(rep_rows).execute()
            total_report += len(rep_rows)
        print(f"  {day}: run_id={run_id} | +{len(new_rows)} new posts (reused {n - len(new_rows)}) | report rows={len(rep_rows)}")

    print(f"\n✅ done: {total_runs} runs, {total_new_posts} new posts, {total_report} report rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
