"""Step 6 data layer store.py — tests (fake Supabase client, no real DB / network / creds required).

Run: python3 system1-app/pipeline/tests/test_store.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from pipeline.runner import run_pipeline  # noqa: E402
from pipeline.store import runresult_to_rows, SupabaseStore  # noqa: E402
from test_runner import StubSource, _reddit_items, _ph_items, NOW  # noqa: E402


# ---- fake supabase client (mimics supabase-py's fluent API) ----
class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self.client, self.table = client, table
        self.op = None
        self.payload = None
        self.filters = {}        # col -> value (eq)
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
        self.active_topics = []   # Preloaded active topics (injected by tests, default empty)
        self.existing_posts = []  # Preloaded posts_archive existing rows (injected by tests, default empty DB)

    def table(self, name):
        return _Query(self, name)

    def _resolve(self, q):
        self.calls.append((q.table, q.op, q.payload))
        if q.table == "topics" and q.op == "select":
            kw = q.filters.get("keyword")
            if kw is not None:    # First query: match by keyword + active
                return [t for t in self.active_topics if t.get("keyword") == kw]
            return list(self.active_topics)   # Second query: any active topic
        if q.table == "topics" and q.op == "insert":
            return [{"topic_id": 1}]
        if q.table == "runs" and q.op == "insert":
            return [{"run_id": 10}]
        if q.table == "posts_archive" and q.op == "select":
            return list(self.existing_posts)  # Mimic "posts already in the DB"
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
        "AI startup",
        [StubSource("reddit", _reddit_items()), StubSource("product_hunt", _ph_items())],
        now=NOW)


def test_runresult_to_rows_shape():
    res = _make_result()
    rows = runresult_to_rows(res)
    assert rows["run"]["topic_keyword"] == "AI startup"
    assert rows["run"]["config_fingerprint"] == res.config_fingerprint
    assert rows["run"]["top20_count"] == len(res.top)
    # Every post row carries source_native_id + config_fingerprint
    for p in rows["posts"]:
        assert p["source_native_id"]
        assert p["config_fingerprint"]
    # Posts in top have ai_review (tier/comment)
    top_keys = {(r["item"].source, r["item"].source_native_id) for r in res.top}
    reviewed = [p for p in rows["posts"]
                if (p["source"], p["source_native_id"]) in top_keys]
    assert reviewed and all(p["ai_review"] and p["ai_review"]["tier"] for p in reviewed)
    # report row shape
    for r in rows["report"]:
        assert r["rank"] >= 1 and r["source_native_id"]
    print("✅ test_runresult_to_rows_shape")


def test_save_uses_real_post_id():
    res = _make_result()
    fake = FakeClient()
    store = SupabaseStore(fake)
    run_id = store.save(res)
    assert run_id == 10

    # runs.insert carries topic_id (payload is a single dict)
    run_ins = [c for c in fake.calls if c[0] == "runs" and c[1] == "insert"][0]
    assert run_ins[2]["topic_id"] == 1

    # Rows inserted into report_top20: post_id must be the real primary key returned by
    # posts_archive upsert (≥1001), not the rank; count = top count; run_id is correct.
    rep_calls = [c for c in fake.calls if c[0] == "report_top20" and c[1] == "insert"]
    assert rep_calls, "should have report_top20 inserts"
    rep_rows = rep_calls[0][2]
    assert len(rep_rows) == len(res.top)
    for r in rep_rows:
        assert r["run_id"] == 10
        assert r["post_id"] >= 1001, f"post_id should be the real PK, not the rank: {r}"
        assert r["rank"] != r["post_id"]   # identity ≠ sort position
    print(f"✅ test_save_uses_real_post_id (report rows={len(rep_rows)}, post_id≥1001)")


def test_tier_mapped_to_schema_short():
    """🐞 Regression (caught live in DB): ai_review emits "强迁移", but report_top20.tier CHECK is 强/中/弱.
    Must map to short names at write time, otherwise the check constraint is violated."""
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
    assert ("starred", "update") in ops   # Soft delete = update deleted_at, not physical delete
    print("✅ test_star_soft_delete_path")


def test_posts_archive_append_only_across_runs():
    """🐞 Regression (Rex 🔴1): posts_archive is an append-only historical snapshot.
    Subsequent runs that re-encounter the same post must only reuse the existing post_id, never
    insert/overwrite historical rows; report_top20 still references the real post_id."""
    res = _make_result()
    rows = runresult_to_rows(res)
    fake = FakeClient()
    # Preload: every post in this run is "already in DB" (mimicking a previous run's writes) with known post_ids
    fake.existing_posts = [
        {"post_id": 2000 + i, "source": p["source"],
         "source_native_id": p["source_native_id"]}
        for i, p in enumerate(rows["posts"])
    ]
    SupabaseStore(fake).save(res)
    # All pre-existing → posts_archive should have zero inserts (any insert would overwrite history)
    post_inserts = [c for c in fake.calls
                    if c[0] == "posts_archive" and c[1] == "insert"]
    assert not post_inserts, f"pre-existing posts must not re-insert (would overwrite history): {post_inserts}"
    # report_top20 still uses the preloaded real post_ids (≥2000)
    rep_rows = [c for c in fake.calls
                if c[0] == "report_top20" and c[1] == "insert"][0][2]
    assert rep_rows and all(r["post_id"] >= 2000 for r in rep_rows), rep_rows
    print("✅ test_posts_archive_append_only_across_runs")


def test_save_inserts_only_new_posts():
    """Mixed scenario: some posts already in DB, some are new → only insert the new ones; old ones reuse post_id."""
    res = _make_result()
    rows = runresult_to_rows(res)
    fake = FakeClient()
    half = rows["posts"][: len(rows["posts"]) // 2]   # First half treated as "already in DB"
    fake.existing_posts = [
        {"post_id": 3000 + i, "source": p["source"],
         "source_native_id": p["source_native_id"]}
        for i, p in enumerate(half)
    ]
    SupabaseStore(fake).save(res)
    post_inserts = [c for c in fake.calls
                    if c[0] == "posts_archive" and c[1] == "insert"]
    assert post_inserts, "new posts should be inserted"
    inserted = post_inserts[0][2]
    assert len(inserted) == len(rows["posts"]) - len(half)
    existing_nids = {p["source_native_id"] for p in half}
    assert all(r["source_native_id"] not in existing_nids for r in inserted)
    print(f"✅ test_save_inserts_only_new_posts (insert {len(inserted)} new)")


def test_report_top20_carries_per_run_comment():
    """🐞 Regression (Rex Step 6 🔴1): per-run review (comment/xhs_title) must land in report_top20,
    not via the append-only posts_archive.ai_review (otherwise a post re-entering the report would
    read a stale review in the UI)."""
    res = _make_result()
    # runresult_to_rows' report rows carry comment
    rows = runresult_to_rows(res)
    assert rows["report"], "should have report rows"
    assert all("comment" in r for r in rows["report"]), "report rows should include the comment field"
    # save() writes report_top20 rows carrying comment (aligned with res.top), non-empty (heuristic has template critique)
    fake = FakeClient()
    SupabaseStore(fake).save(res)
    rep_rows = [c for c in fake.calls
                if c[0] == "report_top20" and c[1] == "insert"][0][2]
    assert all("comment" in r for r in rep_rows), "inserted report_top20 rows should carry comment"
    assert any(r["comment"] for r in rep_rows), "at least some comments non-empty (heuristic template)"
    # comment originates from this run's top (per-run), not from a posts_archive snapshot
    top_comments = {r["comment"] for r in res.top}
    assert {r["comment"] for r in rep_rows} <= top_comments
    print("✅ test_report_top20_carries_per_run_comment")


def test_ensure_topic_reuses_matching_active():
    """Same-keyword active topic exists → reuse, don't create a new one."""
    fake = FakeClient()
    fake.active_topics = [{"topic_id": 7, "keyword": "AI startup"}]
    assert SupabaseStore(fake).ensure_topic("AI startup") == 7
    assert not [c for c in fake.calls
                if c[0] == "topics" and c[1] == "insert"], "reuse should not create a new topic"
    print("✅ test_ensure_topic_reuses_matching_active")


def test_ensure_topic_other_active_fails_loud():
    """🐞 Regression (Rex 🔴2): a different-keyword active topic exists → fail loud;
    don't implicitly switch topics inside save() or wrongly create a new topic (would collide with uq_topics_one_active)."""
    fake = FakeClient()
    fake.active_topics = [{"topic_id": 7, "keyword": "another topic"}]
    store = SupabaseStore(fake)
    raised = False
    try:
        store.ensure_topic("AI startup")
    except RuntimeError:
        raised = True
    assert raised, "should raise RuntimeError when a different active topic exists"
    assert not [c for c in fake.calls
                if c[0] == "topics" and c[1] == "insert"], "should not mistakenly create a topic"
    print("✅ test_ensure_topic_other_active_fails_loud")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
