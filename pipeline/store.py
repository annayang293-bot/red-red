"""Step 6 数据层:RunResult → Supabase(runs / posts_archive / report_top20)+ 精选库读写。

设计:
- `runresult_to_rows(res)`:纯映射(RunResult → 行 dict),无 IO,可单测。
- `SupabaseStore(client)`:包 supabase-py 客户端真写库(需 SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY)。
  client 注入 → 测试用 fake、不依赖真实 DB / 网络 / 凭据。

关键(收 deferred):posts_archive 用 **UNIQUE(source, source_native_id)** upsert → 拿回真
`post_id`;report_top20 / starred 一律引真 `post_id`(不再用 URL-hash / rank 当身份)。
真实 client:`from supabase import create_client; create_client(url, service_role_key)`。
"""
from __future__ import annotations

from typing import Any, Optional

# AI 点评用全名("强迁移"…),但 report_top20.tier 的 CHECK 约束是短名('强'/'中'/'弱')。
# 写库时映射到短名(schema 单一真相);ai_review JSONB 无约束,保留全名(更描述性)。
TIER_DB = {"强迁移": "强", "中等迁移": "中", "弱迁移": "弱"}


def _tier_db(tier):
    return TIER_DB.get(tier, tier)


# ---------------- 纯映射(可单测,无 IO) ----------------
def _post_row(it, fp: str, ai: Optional[dict]) -> dict:
    """HotItem → posts_archive 行(不含 run_id / post_id,由写库时填/取)。"""
    sn = it.source_native or {}
    return {
        "source": it.source,
        "source_native_id": it.source_native_id,
        "title": it.title,
        "url": it.url,
        "raw_snippet": it.raw_snippet,
        "raw_metrics": it.raw_metrics,
        "hot_score": it.hot_score,
        "relevance_score": it.relevance_score,
        "tags_json": it.tags,
        "ai_review": ai,                       # {tier, comment} 仅 top 内的有
        "published_at": it.published_at,
        "fetched_at": it.captured_at,
        "config_fingerprint": sn.get("config_fingerprint", fp),
        "source_native": sn,
        "full_content_url": None,              # 全文走 Storage,后续接
    }


def runresult_to_rows(res) -> dict:
    """RunResult → {run, posts, report}。report 用 (source, source_native_id) 指代帖子,
    写库 upsert 后再换成真 post_id(见 SupabaseStore.save)。"""
    # top 里每条的 ai 点评,按 (source, native_id) 索引
    ai_by_key = {
        (r["item"].source, r["item"].source_native_id): {
            "tier": r["tier"], "comment": r["comment"]}
        for r in res.top
    }
    run = {
        "topic_keyword": res.topic,
        "triggered_by": res.triggered_by,
        "status": res.status,
        "started_at": res.run_at,
        "finished_at": res.run_at,
        "posts_count": res.scored_count,
        "top20_count": len(res.top),
        "ai_mode": res.ai_mode,
        "sanity_status": res.sanity.get("status"),
        "sanity_anomalies": res.sanity.get("anomalies"),
        "config_fingerprint": res.config_fingerprint,
        "error_message": None,
    }
    # 要落 posts_archive 的帖子 = scored 集 ∪ report 里的帖子。
    # (select_ranked 的 PH 配额项可能不在 scored 集,但出现在 report;report_top20.post_id
    #  是 FK,这些帖子必须也在 posts_archive,否则外键挂掉/报告条目丢失。)
    seen: set = set()
    union = []
    for it in list(res.posts) + [r["item"] for r in res.top]:
        k = (it.source, it.source_native_id)
        if k in seen:
            continue
        seen.add(k)
        union.append(it)
    posts = [
        _post_row(it, res.config_fingerprint,
                  ai_by_key.get((it.source, it.source_native_id)))
        for it in union
    ]
    # report 行带 per-run 点评(comment / xhs_title)——这些随 run 变,落 report_top20,
    # 不落 append-only 的 posts_archive(否则同帖二次进报告会读到首次旧点评,Rex 🔴1)。
    report = [
        {"rank": r["rank"], "tier": r["tier"],
         "comment": r.get("comment"), "xhs_title": r.get("xhs_title"),
         "source": r["item"].source, "source_native_id": r["item"].source_native_id}
        for r in res.top
    ]
    return {"run": run, "posts": posts, "report": report}


# ---------------- Supabase 写/读(client 注入) ----------------
class SupabaseStore:
    def __init__(self, client: Any):
        self.c = client

    def _exec(self, q):
        res = q.execute()
        return getattr(res, "data", res)

    def ensure_topic(self, keyword: str) -> int:
        """解析本次 run 归属的 topic_id —— 与"同时刻最多 1 个 active topic"模型一致。

        规则(不在 save 里隐式切主题 / 不复用 archived):
          1) 有匹配 keyword 的 active topic → 用它(正常情形)
          2) 已存在别的 keyword 的 active topic → fail loud(切主题须经 topic 管理层显式操作)
          3) 全局无任何 active topic → 为此 keyword 建一个 active(不会撞 uq_topics_one_active)
        """
        match = self._exec(
            self.c.table("topics").select("topic_id")
            .eq("keyword", keyword).eq("status", "active").limit(1))
        if match:
            return match[0]["topic_id"]
        other = self._exec(
            self.c.table("topics").select("topic_id,keyword")
            .eq("status", "active").limit(1))
        if other:
            raise RuntimeError(
                f"已有 active topic(keyword={other[0].get('keyword')!r}),与本次 run "
                f"keyword={keyword!r} 不符;切主题需经 topic 管理层显式操作,不在 save() 里隐式建/切。"
                " 可显式传 topic_id=... 给 save()。")
        created = self._exec(self.c.table("topics").insert(
            {"keyword": keyword, "status": "active"}))
        return created[0]["topic_id"]

    def save(self, res, topic_id: Optional[int] = None) -> int:
        """把一次 RunResult 落库:topic → run → posts → report_top20(用真 post_id)。返回 run_id。

        posts_archive 是 **append-only 历史快照**(Rex 🔴1):同一帖子(source, source_native_id)
        只在首次出现时 insert;后续 run 再遇到只复用已有 post_id,**绝不覆写历史行**。
        (run_id / hot_score / relevance_score / ai_review / config_fingerprint 都是
         "那一次 run 的快照";blanket upsert 会把旧 run 的历史悄悄改写 → report_top20 join
         回 posts_archive 时历史视图失真。所以这里改成"查已有 → 只插新"。)

        topic_id 可显式传(切主题等场景由上层决定);不传则走 ensure_topic 的保守解析。
        """
        rows = runresult_to_rows(res)
        if topic_id is None:
            topic_id = self.ensure_topic(res.topic)

        run_row = dict(rows["run"], topic_id=topic_id)
        run_id = self._exec(self.c.table("runs").insert(run_row))[0]["run_id"]

        # posts_archive:先查已存在(按 source_native_id),只 insert 没见过的帖子,
        # 已存在的复用其 post_id —— 历史行只写一次,永不覆写。
        key_to_pid: dict[tuple, int] = {}
        if rows["posts"]:
            nids = [p["source_native_id"] for p in rows["posts"]]
            existing = self._exec(
                self.c.table("posts_archive")
                .select("post_id,source,source_native_id")
                .in_("source_native_id", nids))
            # UNIQUE 是 (source, source_native_id),按二元组建索引
            #(in_ 可能多回别的 source 同 native_id 的行,按二元组取就不会错配)
            for r in existing:
                key_to_pid[(r["source"], r["source_native_id"])] = r["post_id"]
            new_rows = [
                dict(p, run_id=run_id) for p in rows["posts"]
                if (p["source"], p["source_native_id"]) not in key_to_pid
            ]
            if new_rows:
                # supabase-py insert 默认 return=representation,直接返回带 post_id 的整行
                saved = self._exec(self.c.table("posts_archive").insert(new_rows))
                for r in saved:
                    key_to_pid[(r["source"], r["source_native_id"])] = r["post_id"]

        # report_top20:引真 post_id(top 帖必在 key_to_pid:要么已存在、要么刚 insert)
        rep_rows = []
        for r in rows["report"]:
            pid = key_to_pid.get((r["source"], r["source_native_id"]))
            if pid is None:
                continue   # 防御性:理论上 union 已保证 top 帖都进了 posts_archive
            rep_rows.append({"run_id": run_id, "post_id": pid,
                             "rank": r["rank"], "tier": _tier_db(r["tier"]),
                             "comment": r.get("comment"), "xhs_title": r.get("xhs_title")})
        if rep_rows:
            self._exec(self.c.table("report_top20").insert(rep_rows))
        return run_id

    # ---- 精选库 ----
    def add_star(self, person: str, post_id: int, run_id: Optional[int] = None) -> None:
        self._exec(self.c.table("starred").insert(
            {"person": person, "post_id": post_id, "run_id": run_id}))

    def remove_star(self, person: str, post_id: int) -> None:
        # 软删:置 deleted_at(配合 partial UNIQUE active)
        from datetime import datetime, timezone
        self._exec(
            self.c.table("starred")
            .update({"deleted_at": datetime.now(timezone.utc).isoformat()})
            .eq("person", person).eq("post_id", post_id).is_("deleted_at", "null"))

    def get_starred(self, person: str) -> list:
        # 按收藏时间倒序(最新在前)—— 前端精选库要稳定顺序,别靠 DB 默认行序(Rex 🟡)。
        return self._exec(
            self.c.table("starred").select("*, posts_archive(*)")
            .eq("person", person).is_("deleted_at", "null")
            .order("starred_at", desc=True))
