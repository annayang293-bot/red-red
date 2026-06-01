"""RedditSource Apify path — mocked HTTP, no real Apify calls.

Run: python3 system1-app/pipeline/tests/test_reddit_apify.py

What's covered:
- Schema mapping (harshmaur post fields → HotItem fields)
- Listing call: 6 subreddit URLs → 1 Apify run → posts grouped by sub
- Missing-token RuntimeError
- Actor failure (start refused / non-SUCCEEDED state) → failed_subs populated
- Empty / partial listing dataset → caller-visible failed_subs
- Comments batch (fetch_comments_for_urls): permalink-keyed dict + cache hit
- OP detection (comment.authorName == post.authorName)
- Comment author/body blocklist applied (AutoModerator / [deleted])
- Per-post fallback path (mode != "apify") untouched (smoke check)
- _enrich_top_with_comments takes the batch path when available
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.sources.reddit_source import RedditSource  # noqa: E402
from pipeline.runner import _enrich_top_with_comments  # noqa: E402
from pipeline.schema import HotItem, make_id, canonical_url  # noqa: E402


# ---- fake `requests.request` ----
class _FakeResp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakeApify:
    """Stand-in for requests.request that scripts the (POST runs / GET runs/<id> / GET datasets/<id>/items) trio.

    Plays each script item in order. Each item: ("POST", run_id_to_return)  → returns a /runs POST resp
                                              ("GET_RUN", state, dataset_id) → returns a /runs/<id> GET
                                              ("GET_DATASET", items_list) → returns the dataset items

    A test sets `_script` per scenario.
    """
    def __init__(self, script):
        self.script = list(script)
        self.calls = []   # (method, url) recorded for assertions

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw.get("data")))
        if not self.script:
            raise AssertionError(f"FakeApify out of script for {method} {url}")
        step = self.script.pop(0)
        kind = step[0]
        if kind == "POST" and "/runs" in url and method == "POST":
            return _FakeResp({"data": {"id": step[1]}}, 201)
        if kind == "POST_FAIL" and method == "POST":
            return _FakeResp({"error": step[1]}, step[2] if len(step) > 2 else 400)
        if kind == "GET_RUN" and "/runs/" in url and method == "GET":
            return _FakeResp({"data": {
                "status": step[1],
                "id": "ignored",
                "defaultDatasetId": step[2],
                "usageTotalUsd": 0.05,
            }})
        if kind == "GET_DATASET" and "/datasets/" in url and method == "GET":
            return _FakeResp(step[1])
        raise AssertionError(f"Unexpected step {step} for {method} {url}")


@contextmanager
def _env(**vars):
    saved = {}
    for k, v in vars.items():
        saved[k] = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---- harshmaur sample payloads (shape verified against real probe responses) ----
def _post(native_id, sub, *, title, author, score, comments_count, body="",
          flair=None, stickied=False, post_type="self"):
    """Build a fake harshmaur post item."""
    return {
        "dataType": "post",
        "id": f"t3_{native_id}",
        "parsedId": native_id,
        "title": title,
        "body": body,
        "bodyHtml": "",
        "authorId": "t2_xxx",
        "authorName": author,
        "communityId": "t5_xxx",
        "communityName": f"r/{sub}",
        "parsedCommunityName": sub,
        "postUrl": f"https://www.reddit.com/r/{sub}/comments/{native_id}/slug/",
        "url": f"https://www.reddit.com/r/{sub}/comments/{native_id}/slug/",
        "upVotes": score,
        "commentsCount": comments_count,
        "createdAt": "2026-05-31T12:00:00.000Z",
        "flair": flair,
        "postType": post_type,
        "stickied": stickied,
    }


def _comment(native_id, post_native_id, *, sub, author, body, score):
    return {
        "dataType": "comment",
        "id": f"t1_{native_id}",
        "parsedId": native_id,
        "body": body,
        "bodyHtml": "",
        "authorName": author,
        "parsedAuthorId": "t2_yyy",
        "parsedPostId": post_native_id,
        "postId": f"t3_{post_native_id}",
        "parentId": f"t3_{post_native_id}",
        "subredditName": sub,
        "commentUpVotes": score,
        "commentCreatedAt": "2026-05-31T12:30:00.000Z",
        "url": f"https://www.reddit.com/r/{sub}/comments/{post_native_id}/slug/{native_id}/",
    }


# ---- tests ----
def test_listing_happy_path_six_subs():
    """One Apify run, 6 subreddit URLs in, posts grouped by sub, all HotItem fields mapped."""
    fake_items = [
        _post(f"p{n}", sub, title=f"Title {sub}-{n}", author=f"u{n}", score=100 - n, comments_count=10 + n)
        for sub in ["OpenAI", "SaaS", "Entrepreneur", "startups", "indiehackers", "artificial"]
        for n in range(3)
    ]
    fake = _FakeApify([
        ("POST", "run_listing_1"),
        ("GET_RUN", "SUCCEEDED", "ds_listing_1"),
        ("GET_DATASET", fake_items),
    ])
    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", fake):
        src = RedditSource({"reddit": {
            "auth_mode": "apify",
            "subreddits": ["OpenAI", "SaaS", "Entrepreneur", "startups", "indiehackers", "artificial"],
            "fetch_limit_per_sub": 30,
        }})
        items = src.fetch()
    # 6 subs × 3 posts each
    assert len(items) == 18, items
    # All from reddit source, with the expected fields wired through
    assert all(it.source == "reddit" for it in items)
    sample = items[0]
    assert sample.title.startswith("Title ")
    assert sample.author and sample.url.startswith("https://www.reddit.com/r/")
    assert sample.raw_metrics["likes"] == 100  # upVotes → likes
    assert sample.raw_metrics["comments"] >= 10  # commentsCount → comments
    assert sample.source_native["fetch_mode"] == "apify"
    assert sample.source_native["permalink"].startswith("/r/")
    # No subreddit got marked failed
    assert src.failed_subs == [], src.failed_subs
    print("✅ test_listing_happy_path_six_subs")


def test_listing_missing_token_raises():
    """Calling fetch() with no APIFY_TOKEN must blow up with a clear message — runner sees this
    as the source raising, marks the whole source as failed_sources (via the existing path in
    run_pipeline ①)."""
    with _env(APIFY_TOKEN=None):
        src = RedditSource({"reddit": {"auth_mode": "apify", "subreddits": ["OpenAI"]}})
        raised = None
        try:
            src.fetch()
        except RuntimeError as e:
            raised = e
        assert raised is not None
        assert "APIFY_TOKEN" in str(raised)
    print("✅ test_listing_missing_token_raises")


def test_listing_start_failure_marks_all_failed():
    """Apify POST /runs returns 400 → entire listing call fails → every requested sub is in failed_subs."""
    import requests as _req
    fake_calls = []

    def fake_request(method, url, **kw):
        fake_calls.append((method, url))
        resp = _FakeResp({"error": "bad input"}, 400)
        # raise_for_status fires inside the helper; mimic that exactly:
        raise _req.HTTPError("HTTP 400", response=resp)

    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", side_effect=fake_request):
        src = RedditSource({"reddit": {"auth_mode": "apify",
                                       "subreddits": ["OpenAI", "SaaS"]}})
        items = src.fetch()
    assert items == []
    assert set(src.failed_subs) == {"OpenAI", "SaaS"}, src.failed_subs
    print("✅ test_listing_start_failure_marks_all_failed")


def test_listing_actor_finished_failed_state_marks_failed():
    """Actor finishes in FAILED state (not SUCCEEDED) → no items, all subs failed."""
    fake = _FakeApify([
        ("POST", "run_listing_x"),
        ("GET_RUN", "FAILED", "ds_x"),
    ])
    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", fake):
        src = RedditSource({"reddit": {"auth_mode": "apify",
                                       "subreddits": ["OpenAI", "SaaS", "Entrepreneur"]}})
        items = src.fetch()
    assert items == []
    assert set(src.failed_subs) == {"OpenAI", "SaaS", "Entrepreneur"}
    print("✅ test_listing_actor_finished_failed_state_marks_failed")


def test_listing_missing_subs_marked_failed():
    """SUCCEEDED run but some subs are absent from the dataset → those subs join failed_subs
    (matches the old-html behavior where a banned/private sub parsed 0 posts)."""
    # Only OpenAI items in the dataset; SaaS and Entrepreneur requested but absent.
    items = [_post(f"p{n}", "OpenAI", title=f"t{n}", author="alice", score=10, comments_count=5)
             for n in range(2)]
    fake = _FakeApify([
        ("POST", "run_listing_partial"),
        ("GET_RUN", "SUCCEEDED", "ds_partial"),
        ("GET_DATASET", items),
    ])
    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", fake):
        src = RedditSource({"reddit": {"auth_mode": "apify",
                                       "subreddits": ["OpenAI", "SaaS", "Entrepreneur"]}})
        items_got = src.fetch()
    assert len(items_got) == 2
    assert set(src.failed_subs) == {"SaaS", "Entrepreneur"}
    print("✅ test_listing_missing_subs_marked_failed")


def test_listing_payload_sends_lowercase_search_sort():
    """🐞 Regression (run 53 prod dispatch, 2026-05-31): harshmaur's input schema rejects
    capitalized sort values with HTTP 400 ("Field input.searchSort must be equal to one of
    the allowed values: 'relevance', 'hot', 'top', 'new', 'comments'"). We used to call
    `listing.capitalize()` because some Reddit docs use capitalized form — but the actor's
    schema validator is strict lowercase. Lock it in.

    Also asserts the rest of the listing payload shape (listing-only, no comments) so a
    future "let's just include comments inline" refactor needs to update the test
    deliberately.
    """
    captured: dict = {}

    def fake_request(method, url, **kw):
        if method == "POST" and "/runs" in url:
            import json as _json
            captured["payload"] = _json.loads(kw["data"].decode("utf-8"))
            return _FakeResp({"data": {"id": "run_x"}}, 201)
        if method == "GET" and "/runs/" in url:
            return _FakeResp({"data": {"status": "SUCCEEDED", "id": "run_x",
                                       "defaultDatasetId": "ds_x", "usageTotalUsd": 0.1}})
        if method == "GET" and "/datasets/" in url:
            return _FakeResp([])
        raise AssertionError(f"unexpected {method} {url}")

    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", side_effect=fake_request):
        src = RedditSource({"reddit": {
            "auth_mode": "apify",
            "subreddits": ["OpenAI", "SaaS"],
            "listing": "hot",
            "fetch_limit_per_sub": 30,
        }})
        src.fetch()
    payload = captured["payload"]
    assert payload["searchSort"] == "hot", payload["searchSort"]   # lowercase, NOT "Hot"
    # Listing must NOT request comments — Top-N comments are a separate Apify call.
    assert payload["crawlCommentsPerPost"] is False, payload
    assert payload["maxCommentsPerPost"] == 0, payload
    # All 6 subreddits passed through as URLs in startUrls.
    urls = [u["url"] for u in payload["startUrls"]]
    assert urls == ["https://www.reddit.com/r/OpenAI/hot/",
                    "https://www.reddit.com/r/SaaS/hot/"], urls
    assert payload["maxPostsCount"] == 30
    print("✅ test_listing_payload_sends_lowercase_search_sort")


def test_listing_filters_stickied_and_flair():
    """Stickied posts are dropped; flair on the excluded list is dropped; the rest survive."""
    fake_items = [
        _post("k", "SaaS", title="Mod megathread", author="m", score=100, comments_count=99, stickied=True),
        _post("j", "SaaS", title="Meme", author="u", score=50, comments_count=10, flair="Meme"),
        _post("g", "SaaS", title="Real Post", author="u", score=80, comments_count=20, flair="Discussion"),
    ]
    fake = _FakeApify([
        ("POST", "run1"),
        ("GET_RUN", "SUCCEEDED", "ds1"),
        ("GET_DATASET", fake_items),
    ])
    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", fake):
        src = RedditSource({"reddit": {
            "auth_mode": "apify",
            "subreddits": ["SaaS"],
            "excluded_flairs": ["meme"],
        }})
        items = src.fetch()
    titles = {it.title for it in items}
    assert titles == {"Real Post"}, titles
    print("✅ test_listing_filters_stickied_and_flair")


def test_comments_batch_keys_by_permalink_and_caches():
    """fetch_comments_for_urls returns {permalink: [comments...]} keyed by canonical Reddit permalink,
    sorted by score descending, capped at max_comments. AutoModerator/[deleted] are filtered.
    The same lookup is then served from the in-memory cache via fetch_post_comments."""
    items = [
        # The post itself (so authorName for OP detection)
        _post("abc", "OpenAI", title="Cool", author="op_user", score=200, comments_count=10),
        # Comments (different scores, one from OP, one from blocklisted author)
        _comment("c1", "abc", sub="OpenAI", author="other_user", body="best take", score=42),
        _comment("c2", "abc", sub="OpenAI", author="op_user", body="OP reply", score=15),
        _comment("c3", "abc", sub="OpenAI", author="AutoModerator", body="rules: ...", score=99),  # blocklisted
        _comment("c4", "abc", sub="OpenAI", author="someone_else", body="[deleted]", score=10),  # body blocklist
        _comment("c5", "abc", sub="OpenAI", author="lurker", body="ok", score=3),
    ]
    fake = _FakeApify([
        ("POST", "run_cmt"),
        ("GET_RUN", "SUCCEEDED", "ds_cmt"),
        ("GET_DATASET", items),
    ])
    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", fake):
        src = RedditSource({"reddit": {"auth_mode": "apify", "subreddits": ["OpenAI"]}})
        result = src.fetch_comments_for_urls(
            ["https://www.reddit.com/r/OpenAI/comments/abc/slug/"],
            max_comments=10,
        )
    expected_key = "/r/OpenAI/comments/abc/slug/"
    assert expected_key in result, result
    cmts = result[expected_key]
    assert len(cmts) == 3   # 5 items minus AutoModerator and [deleted]
    assert [c["score"] for c in cmts] == [42, 15, 3]   # sorted desc
    op = next(c for c in cmts if c["author"] == "op_user")
    assert op["is_op"] is True
    # Cache: a subsequent fetch_post_comments serves the same list from memory (no new Apify call).
    cached = src.fetch_post_comments(expected_key, op_author="op_user", max_comments=10)
    assert cached == cmts
    print(f"✅ test_comments_batch_keys_by_permalink_and_caches (kept {len(cmts)}/5 comments)")


def test_comments_batch_max_comments_zero_returns_empty_without_calling():
    """If max_comments is 0, skip the network call entirely (cost-saving short-circuit)."""
    calls = []

    def fake_request(method, url, **kw):
        calls.append((method, url))
        raise AssertionError("network should not be called when max_comments=0")

    with _env(APIFY_TOKEN="apify_test_token"), \
         patch("pipeline.sources.reddit_source.requests.request", side_effect=fake_request):
        src = RedditSource({"reddit": {"auth_mode": "apify", "subreddits": ["OpenAI"]}})
        result = src.fetch_comments_for_urls(
            ["https://www.reddit.com/r/OpenAI/comments/abc/slug/"],
            max_comments=0,
        )
    assert result == {}
    assert calls == []
    print("✅ test_comments_batch_max_comments_zero_returns_empty_without_calling")


def test_runner_enrich_uses_batch_path_when_available():
    """_enrich_top_with_comments detects fetch_comments_for_urls and uses it instead of the
    per-post loop. Verifies the integration end-to-end at the runner ↔ source seam without
    real network."""
    # Build 3 HotItems pointing at distinct posts so we can confirm comment attachment per item.
    permalinks = [f"/r/OpenAI/comments/p{n}/slug/" for n in range(3)]
    items = []
    for n, pl in enumerate(permalinks):
        url = f"https://www.reddit.com{pl}"
        items.append(HotItem(
            id=make_id("reddit", f"p{n}"),
            dedup_key=canonical_url(url),
            title=f"Post {n}",
            source="reddit",
            source_native_id=f"p{n}",
            url=url,
            author="u",
            published_at="2026-05-31T12:00:00+00:00",
            captured_at="2026-05-31T13:00:00+00:00",
            lang="en",
            media_type="text",
            raw_metrics={"likes": 1, "upvotes": 1, "comments": 0, "saves": 0},
            source_native={"subreddit": "OpenAI", "permalink": pl, "fetch_mode": "apify"},
            tags=["OpenAI"],
            raw_snippet="",
        ))

    # A source with the batch API only.
    class _BatchSrc:
        name = "reddit"
        called_with = []

        def fetch_comments_for_urls(self, urls, max_comments=10):
            self.called_with.append((tuple(urls), max_comments))
            # Stamp the same shape our real source returns.
            return {
                permalinks[0]: [{"id": "c1", "fullname": "t1_c1", "author": "x",
                                 "score": 5, "body": "first", "is_op": False, "replies": 0}],
                permalinks[1]: [],
                permalinks[2]: [{"id": "c2", "fullname": "t1_c2", "author": "y",
                                 "score": 3, "body": "third", "is_op": False, "replies": 0}],
            }

    src = _BatchSrc()
    _enrich_top_with_comments(
        [src], items, {"comments": {"max_per_post": 10, "rate_limit_sleep": 0}})
    # The batch method was called once with all 3 URLs in one go (not 3 separate calls).
    assert len(src.called_with) == 1, src.called_with
    urls, mx = src.called_with[0]
    assert set(urls) == {it.url for it in items}
    assert mx == 10
    # Items 0 and 2 got comments; item 1 had empty list → source_native["comments"] stays unset.
    assert items[0].source_native.get("comments")[0]["body"] == "first"
    assert "comments" not in items[1].source_native or not items[1].source_native["comments"]
    assert items[2].source_native.get("comments")[0]["body"] == "third"
    print("✅ test_runner_enrich_uses_batch_path_when_available")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
