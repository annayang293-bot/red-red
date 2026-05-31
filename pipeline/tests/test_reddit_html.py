"""Tests for the old.reddit.com HTML parser (Anna 2026-05-31, option D bypass).

Reddit's 2025-11 anti-bot policy 403s the JSON API for non-residential IPs but `old.reddit.com`
HTML still returns 200 with vote data embedded as `data-*` attributes. These tests pin the
parser's behavior on representative fixture HTML so a Reddit markup change shows up loudly.

Fixtures are small hand-stripped snippets mirroring the live shape — enough to exercise every
field path without dragging in 168KB of unrelated CSS/JS.

Run: python3 system1-app/pipeline/tests/test_reddit_html.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.sources.reddit_source import parse_old_reddit_html  # noqa: E402


# A normal post: positive score, no sticky, with a flair.
NORMAL_POST_HTML = """
<div class="thing id-t3_abc123 link" data-fullname="t3_abc123"
     data-author="alice" data-permalink="/r/SaaS/comments/abc123/my_first_dollar/"
     data-score="535" data-comments-count="186" data-timestamp="1748725823000"
     data-subreddit="SaaS">
  <p class="title">
    <a class="title may-blank" href="/r/SaaS/comments/abc123/">My first dollar — what I learned</a>
    <span class="linkflairlabel" title="Share My Story">Share My Story</span>
  </p>
</div>
"""

# A stickied megathread: should be flagged so the pipeline filters it out.
STICKIED_POST_HTML = """
<div class="thing id-t3_meg123 link stickied" data-fullname="t3_meg123"
     data-author="OpenAI" data-permalink="/r/OpenAI/comments/meg123/megathread/"
     data-score="315" data-comments-count="9772" data-timestamp="1739999000000"
     data-subreddit="OpenAI">
  <p class="title"><a class="title" href="/r/OpenAI/comments/meg123/">Sora 2 megathread</a></p>
</div>
"""

# Negative score post (downvoted) — make sure we don't drop the sign.
NEGATIVE_SCORE_HTML = """
<div class="thing id-t3_neg link" data-fullname="t3_neg"
     data-author="badtake" data-permalink="/r/Entrepreneur/comments/neg/bad_idea/"
     data-score="-12" data-comments-count="3" data-timestamp="1748000000000"
     data-subreddit="Entrepreneur">
  <p class="title"><a class="title" href="/r/Entrepreneur/comments/neg/">My genuinely bad idea</a></p>
</div>
"""

# Post with no flair → flair field should be empty string.
NO_FLAIR_HTML = """
<div class="thing id-t3_nof link" data-fullname="t3_nof"
     data-author="quiet" data-permalink="/r/startups/comments/nof/x/"
     data-score="7" data-comments-count="0" data-timestamp="1748100000000"
     data-subreddit="startups">
  <p class="title"><a class="title" href="/r/startups/comments/nof/">Quiet post</a></p>
</div>
"""


def test_parse_normal_post_all_fields():
    """🐞 Every field extracted correctly from a representative .thing block."""
    posts = parse_old_reddit_html(NORMAL_POST_HTML)
    assert len(posts) == 1, posts
    p = posts[0]
    assert p["id"] == "abc123"                              # t3_ prefix stripped
    assert p["fullname"] == "t3_abc123"
    assert p["title"] == "My first dollar — what I learned"
    assert p["author"] == "alice"
    assert p["permalink"] == "/r/SaaS/comments/abc123/my_first_dollar/"
    assert p["score"] == 535
    assert p["comments"] == 186
    assert p["timestamp_ms"] == 1748725823000
    assert p["subreddit"] == "SaaS"
    assert p["is_stickied"] is False
    assert p["flair"] == "Share My Story"
    print("✅ test_parse_normal_post_all_fields")


def test_stickied_post_flagged():
    """🐞 Stickied posts (megathreads / mod pins) must carry is_stickied=True so the pipeline
    can filter them out — they're 100+ days old and dominate the engagement curve otherwise."""
    posts = parse_old_reddit_html(STICKIED_POST_HTML)
    assert len(posts) == 1
    assert posts[0]["is_stickied"] is True, posts[0]
    print("✅ test_stickied_post_flagged")


def test_negative_score_preserved():
    """🐞 Negative scores (downvoted posts) must be preserved as negative integers; the regex
    needs to accept the `-` sign."""
    posts = parse_old_reddit_html(NEGATIVE_SCORE_HTML)
    assert len(posts) == 1
    assert posts[0]["score"] == -12, posts[0]
    print("✅ test_negative_score_preserved")


def test_no_flair_returns_empty_string():
    """🐞 When the post has no flair, the field should be '' (not None) — keeps downstream
    string ops simple."""
    posts = parse_old_reddit_html(NO_FLAIR_HTML)
    assert len(posts) == 1
    assert posts[0]["flair"] == "", posts[0]
    print("✅ test_no_flair_returns_empty_string")


def test_empty_html_returns_empty_list():
    """🐞 Empty or non-post HTML → empty list (caller filters by length)."""
    assert parse_old_reddit_html("") == []
    assert parse_old_reddit_html("<html><body>nothing here</body></html>") == []
    print("✅ test_empty_html_returns_empty_list")


def test_multiple_posts_in_one_page():
    """🐞 Real pages have 25-100 posts — the parser must scan them all in document order."""
    combined = NORMAL_POST_HTML + STICKIED_POST_HTML + NEGATIVE_SCORE_HTML + NO_FLAIR_HTML
    posts = parse_old_reddit_html(combined)
    assert len(posts) == 4
    # Order preserved
    assert [p["id"] for p in posts] == ["abc123", "meg123", "neg", "nof"]
    # Filter sticky in caller — verify the flag is set per-post
    nonsticky = [p for p in posts if not p["is_stickied"]]
    assert [p["id"] for p in nonsticky] == ["abc123", "neg", "nof"]
    print("✅ test_multiple_posts_in_one_page")


def test_html_with_unrelated_data_fullname_doesnt_match():
    """🐞 The page contains other data-fullname references (comments, user widgets) — those use
    t1_ / t5_ prefixes, NOT t3_. The post regex anchors on t3_ specifically."""
    snippet = """
    <div data-fullname="t1_commentid">a comment reference</div>
    <div data-fullname="t5_subredditid">a sub reference</div>
    """ + NORMAL_POST_HTML
    posts = parse_old_reddit_html(snippet)
    assert len(posts) == 1
    assert posts[0]["fullname"].startswith("t3_")
    print("✅ test_html_with_unrelated_data_fullname_doesnt_match")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
