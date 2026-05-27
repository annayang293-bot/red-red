"""Step 6 数据层 store.py — 测试(fake Supabase client,不依赖真实 DB/网络/凭据)。

跑法: python3 system1-app/pipeline/tests/test_store.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from pipeline.runner import run_pipeline  # noqa: E402
from pipeline.store import runresult_to_rows, SupabaseStore  # noqa: E402
from test_runner import StubSource, _reddit_items, _ph_items, NOW  # noqa: E402


# ---- fake supabase client(模拟 supabase-py 的 fluent API)----
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op = None
        self.payload = None
        self.filters = {}        # col -> value(eq)
        self.in_filter = None    # (col, [values])

    def select(self, cols="*"):
        self.op = self.op or "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, vals):
        self.op, self.payload = "update", vals
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def in_(self, col, vals):
        self.in_filter = (col, list(vals))
        return self

    def is_(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def execute(self):
        return _Result(self.client._resolve(self))


class FakeClient:
    def __init__(self):
        self.calls = []           # (table, op, payload)
        self._pid = 1000
        self.active_topics = []   # 预置的 active topics(test 注入,默认无)
        self.existing_posts = []  # 预置的 posts_archive 既有行(test 注入,默认空库)

    def table(self, name):
        return _Query(self, name)

    def _resolve(self, q):
        self.calls.append((q.table, q.op, q.payload))
        if q.table == "topics" and q.op == "select":
            kw = q.filters.get("keyword")
            if kw is not None:    # 第一查:按 keyword + active 匹配
                return [t for t in self.active_topics if t.get("keyword") == kw]
            return list(self.active_topics)   # 第二查:任意 active topic
        if q.table == "topics" and q.op == "insert":
            return [{"topic_id": 1}]
        if q.table == "runs" and q.op == "insert":
            return [{"run_id": 10}]
        if q.table == "posts_archive" and q.op == "select":
            return list(self.existing_posts)  # 模拟"库里已有的帖子"
        if q.table == "posts_archive" and q.op == "insert":
            out = []
            for row in q.payload:
                self._pid += 1
                out.append({"post_id": self._pid, "source": row["source"],
                            "source_native_id": row["source_native_id"]})
            return out
        return []


def _make_result():
    return run_pipeline(
        "AI 创业",
        [StubSource("reddit", _reddit_items()), StubSource("product_hunt", _ph_items())],
        now=NOW)


def test_runresult_to_rows_shape():
    res = _make_result()
    rows = runresult_to_rows(res)
    assert rows["run"]["topic_keyword"] == "AI 创业"
    assert rows["run"]["config_fingerprint"] == res.config_fingerprint
    assert rows["run"]["top20_count"] == len(res.top)
    # 每个 post 行带 source_native_id + config_fingerprint
    for p in rows["posts"]:
        assert p["source_native_id"]
        assert p["config_fingerprint"]
    # top 里的帖子有 ai_review(tier/comment)
    top_keys = {(r["item"].source, r["item"].source_native_id) for r in res.top}
    reviewed = [p for p in rows["posts"]
                if (p["source"], p["source_native_id"]) in top_keys]
    assert reviewed and all(p["ai_review"] and p["ai_review"]["tier"] for p in reviewed)
    # report 行结构
    for r in rows["report"]:
        assert r["rank"] >= 1 and r["source_native_id"]
    print("✅ test_runresult_to_rows_shape")


def test_save_uses_real_post_id():
    res = _make_result()
    fake = FakeClient()
    store = SupabaseStore(fake)
    run_id = store.save(res)
    assert run_id == 10

    # runs.insert 带 topic_id(payload 是单个 dict)
    run_ins = [c for c in fake.calls if c[0] == "runs" and c[1] == "insert"][0]
    assert run_ins[2]["topic_id"] == 1

    # report_top20 插入的行:post_id 必须是 posts_archive upsert 回来的真主键(≥1001),
    # 不是 rank;数量 = top 数;run_id 正确
    rep_calls = [c for c in fake.calls if c[0] == "report_top20" and c[1] == "insert"]
    assert rep_calls, "应有 report_top20 插入"
    rep_rows = rep_calls[0][2]
    assert len(rep_rows) == len(res.top)
    for r in rep_rows:
        assert r["run_id"] == 10
        assert r["post_id"] >= 1001, f"post_id 应是真主键而非 rank: {r}"
        assert r["rank"] != r["post_id"]   # 身份 ≠ 排序位
    print(f"✅ test_save_uses_real_post_id (report rows={len(rep_rows)}, post_id≥1001)")


def test_tier_mapped_to_schema_short():
    """🐞 回归(live DB 抓到):ai_review 出"强迁移",但 report_top20.tier CHECK 是 强/中/弱。
    写库时必须映射成短名,否则违反 check 约束。"""
    from pipeline.store import _tier_db
    assert _tier_db("强迁移") == "强"
    assert _tier_db("中等迁移") == "中"
    assert _tier_db("弱迁移") == "弱"
    res = _make_result()
    fake = FakeClient()
    SupabaseStore(fake).save(res)
    rep_rows = [c for c in fake.calls if c[0] == "report_top20" and c[1] == "insert"][0][2]
    assert all(r["tier"] in ("强", "中", "弱") for r in rep_rows), rep_rows
    print("✅ test_tier_mapped_to_schema_short")


def test_star_soft_delete_path():
    fake = FakeClient()
    store = SupabaseStore(fake)
    store.add_star("anna", 1005, run_id=10)
    store.remove_star("anna", 1005)
    ops = [(c[0], c[1]) for c in fake.calls]
    assert ("starred", "insert") in ops
    assert ("starred", "update") in ops   # 软删 = update deleted_at,不是物理删
    print("✅ test_star_soft_delete_path")


def test_posts_archive_append_only_across_runs():
    """🐞 回归(Rex 🔴1):posts_archive 是 append-only 历史快照。
    后续 run 再遇到同一帖子,只复用已有 post_id,绝不 insert/覆写历史行;
    report_top20 仍引真 post_id。"""
    res = _make_result()
    rows = runresult_to_rows(res)
    fake = FakeClient()
    # 预置:本次所有帖子都"已在库"(模拟上一轮 run 已写入),给定已知 post_id
    fake.existing_posts = [
        {"post_id": 2000 + i, "source": p["source"],
         "source_native_id": p["source_native_id"]}
        for i, p in enumerate(rows["posts"])
    ]
    SupabaseStore(fake).save(res)
    # 全部已存在 → posts_archive 不应有任何 insert(否则就是覆写历史)
    post_inserts = [c for c in fake.calls
                    if c[0] == "posts_archive" and c[1] == "insert"]
    assert not post_inserts, f"已存在的帖子不应再 insert(会覆写历史): {post_inserts}"
    # report_top20 仍用预置的真 post_id(≥2000)
    rep_rows = [c for c in fake.calls
                if c[0] == "report_top20" and c[1] == "insert"][0][2]
    assert rep_rows and all(r["post_id"] >= 2000 for r in rep_rows), rep_rows
    print("✅ test_posts_archive_append_only_across_runs")


def test_save_inserts_only_new_posts():
    """混合场景:部分帖子已在库、部分是新的 → 只 insert 新的,旧的复用 post_id。"""
    res = _make_result()
    rows = runresult_to_rows(res)
    fake = FakeClient()
    half = rows["posts"][: len(rows["posts"]) // 2]   # 一半当作"已在库"
    fake.existing_posts = [
        {"post_id": 3000 + i, "source": p["source"],
         "source_native_id": p["source_native_id"]}
        for i, p in enumerate(half)
    ]
    SupabaseStore(fake).save(res)
    post_inserts = [c for c in fake.calls
                    if c[0] == "posts_archive" and c[1] == "insert"]
    assert post_inserts, "应当 insert 新帖子"
    inserted = post_inserts[0][2]
    assert len(inserted) == len(rows["posts"]) - len(half)
    existing_nids = {p["source_native_id"] for p in half}
    assert all(r["source_native_id"] not in existing_nids for r in inserted)
    print(f"✅ test_save_inserts_only_new_posts (insert {len(inserted)} new)")


def test_report_top20_carries_per_run_comment():
    """🐞 回归(Rex Step6 🔴1):per-run 点评(comment/xhs_title)必须落 report_top20,
    不靠 append-only 的 posts_archive.ai_review(否则同帖二次进报告 UI 会读到旧点评)。"""
    res = _make_result()
    # runresult_to_rows 的 report 行带 comment
    rows = runresult_to_rows(res)
    assert rows["report"], "应有 report 行"
    assert all("comment" in r for r in rows["report"]), "report 行应带 comment 字段"
    # save() 写进 report_top20 的行带 comment(与 res.top 对齐),且非空(heuristic 有模板点评)
    fake = FakeClient()
    SupabaseStore(fake).save(res)
    rep_rows = [c for c in fake.calls
                if c[0] == "report_top20" and c[1] == "insert"][0][2]
    assert all("comment" in r for r in rep_rows), "report_top20 插入行应含 comment"
    assert any(r["comment"] for r in rep_rows), "至少部分 comment 非空(heuristic 模板)"
    # comment 来自本次 run 的 top(per-run),不是 posts_archive 快照
    top_comments = {r["comment"] for r in res.top}
    assert {r["comment"] for r in rep_rows} <= top_comments
    print("✅ test_report_top20_carries_per_run_comment")


def test_ensure_topic_reuses_matching_active():
    """有同 keyword 的 active topic → 复用,不建新。"""
    fake = FakeClient()
    fake.active_topics = [{"topic_id": 7, "keyword": "AI 创业"}]
    assert SupabaseStore(fake).ensure_topic("AI 创业") == 7
    assert not [c for c in fake.calls
                if c[0] == "topics" and c[1] == "insert"], "复用时不应建新 topic"
    print("✅ test_ensure_topic_reuses_matching_active")


def test_ensure_topic_other_active_fails_loud():
    """🐞 回归(Rex 🔴2):已有别的 keyword 的 active topic → fail loud,
    不在 save 里隐式切主题、也不误建新 topic(会撞 uq_topics_one_active)。"""
    fake = FakeClient()
    fake.active_topics = [{"topic_id": 7, "keyword": "别的主题"}]
    store = SupabaseStore(fake)
    raised = False
    try:
        store.ensure_topic("AI 创业")
    except RuntimeError:
        raised = True
    assert raised, "存在别的 active topic 时应抛 RuntimeError"
    assert not [c for c in fake.calls
                if c[0] == "topics" and c[1] == "insert"], "不应误建 topic"
    print("✅ test_ensure_topic_other_active_fails_loud")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
