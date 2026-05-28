"""One-shot migration: lift hotspots from the legacy system1-scraper/data/system1.db into Supabase.

Strategy: group by DATE(captured_at) → one runs row per day (triggered_by=cron,
person=legacy_import) → each hotspot lands in posts_archive (append-only; native_id extracted from URL);
each day's top 20 by hot_score lands in report_top20, tier assigned heuristically by percentile
(top 34% = strong / middle 40% = medium / bottom 26% = weak).

Conservative (in the spirit of Rex's data-layer review):
- posts_archive uses (source, source_native_id) UNIQUE to prevent duplicates — look up existing first, only insert new.
- Failed / unidentifiable native-id rows (can't extract from URL) → skipped + counted.
- Legacy SQLite has no AI critique → ai_review / comment / xhs_title all NULL, ai_mode='heuristic'.

Run: python3 system1-app/scripts/migrate_legacy_sqlite.py [--dry-run]
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
TIER_DB = {"强": "强", "中": "中", "弱": "弱"}   # report_top20.tier CHECK short names

# Reddit url → post native id extraction (/comments/<id>/...)
_REDDIT_ID_RE = re.compile(r"/comments/([a-z0-9]+)/", re.I)


def extract_native_id(source: str, url: str | None, source_native: dict | None) -> str | None:
    """Extract native_id from the url or source_native. Reddit uses /comments/<id>/."""
    if not url:
        return None
    if source == "reddit":
        m = _REDDIT_ID_RE.search(url)
        if m:
            return m.group(1)
        # Fallback: source_native.permalink
        perm = (source_native or {}).get("permalink") or ""
        m = _REDDIT_ID_RE.search(perm)
        return m.group(1) if m else None
    if source == "product_hunt":
        # PH url patterns /products/<slug>/ or /posts/<slug>; legacy SQLite has no PH in practice, kept as fallback.
        for prefix in ("/products/", "/posts/"):
            i = url.find(prefix)
            if i >= 0:
                rest = url[i + len(prefix):].split("/")[0].split("?")[0].strip()
                if rest:
                    return rest
        return None
    return None


def heuristic_tier_short(rank_in_day: int, n_in_day: int) -> str:
    """Same percentile logic as heuristic_review, returning report_top20's short names (强/中/弱)."""
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
    ap.add_argument("--dry-run", action="store_true", help="Print the plan only, don't write to DB")
    args = ap.parse_args(argv)

    if not LEGACY_DB.exists():
        print(f"❌ legacy DB not found: {LEGACY_DB}", file=sys.stderr)
        return 1

    # ---- Read legacy ----
    conn = sqlite3.connect(str(LEGACY_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM hotspots ORDER BY captured_at").fetchall()
    print(f"legacy: {len(rows)} hotspots")

    # Group by day (YYYY-MM-DD)
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
        print(f"⚠️ {skipped_no_id} rows could not yield a native_id (skipped)")
    days = sorted(by_day.keys())
    print(f"grouped into {len(days)} days: {days[0]} → {days[-1]}")
    for d in days:
        print(f"  {d}: {len(by_day[d])} posts")

    if args.dry_run:
        print("\n--dry-run, not writing, exiting")
        return 0

    # ---- Connect to Supabase ----
    c = get_client()
    # Legacy data is attributed to the "AI 创业" topic (legacy has been running it); active or archived is fine here.
    target = c.table("topics").select("topic_id,status").eq("keyword", "AI 创业") \
              .order("started_at", desc=True).limit(1).execute().data
    if not target:
        print("❌ no topic with keyword='AI 创业' found (need to run/create one in the UI first)", file=sys.stderr)
        return 1
    topic_id = target[0]["topic_id"]
    print(f"legacy data attributed to topic 'AI 创业' (topic_id={topic_id}, status={target[0]['status']})")

    # Existing posts (avoid re-inserting): query against this run's set of native_ids
    all_nids = [p["native_id"] for d in days for p in by_day[d]]
    existing: dict[tuple, int] = {}
    BATCH = 200
    for i in range(0, len(all_nids), BATCH):
        chunk = all_nids[i:i + BATCH]
        ex = c.table("posts_archive").select("post_id,source,source_native_id") \
              .in_("source_native_id", chunk).execute().data
        for r in ex:
            existing[(r["source"], r["source_native_id"])] = r["post_id"]
    print(f"existing related posts in DB: {len(existing)} (will be skipped on insert)")

    # ---- Import day by day ----
    total_runs = total_new_posts = total_report = 0
    for day in days:
        posts = by_day[day]
        # Within a day, sort by hot_score descending
        posts.sort(key=lambda p: float(p["row"]["hot_score"] or 0), reverse=True)
        n = len(posts)
        # Use the day's earliest captured_at as started_at (otherwise 06:00 LA also works)
        started_at = posts[-1]["row"]["captured_at"] or f"{day}T13:00:00+00:00"  # 06:00 LA = 13:00 UTC

        run_row = {
            "topic_id": topic_id, "topic_keyword": "AI 创业",
            "triggered_by": "cron", "triggered_by_person": "legacy_import",
            "status": "completed",
            "started_at": started_at, "finished_at": started_at,
            "posts_count": n, "top20_count": min(20, n),
            "ai_mode": "heuristic",                 # legacy doesn't have per-post AI
            "sanity_status": "OK",
            "config_fingerprint": f"legacy_import_{day}",
        }
        run_id = c.table("runs").insert(run_row).execute().data[0]["run_id"]
        total_runs += 1

        # Prepare this day's posts_archive inserts (new only)
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

        # report_top20: take top 20 of the day (posts already sorted by hot_score desc)
        top = posts[:20]
        rep_rows = []
        for i, p in enumerate(top, start=1):
            key = (p["row"]["source"], p["native_id"])
            pid = key_to_pid.get(key)
            if pid is None:
                continue   # Defensive
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
