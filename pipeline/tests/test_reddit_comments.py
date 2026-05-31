"""Tests for the old.reddit comment-page parser (Anna 2026-05-31, option D enrichment).

Locks the comment parser's behavior on representative fixture HTML so Reddit markup drift shows up
in CI before it silently breaks production runs.

Run: python3 system1-app/pipeline/tests/test_reddit_comments.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.sources.reddit_source import parse_old_reddit_comments  # noqa: E402


# Hand-stripped fixture mirroring old.reddit's actual <div data-type="comment"> shape. The
# essential fields are data-fullname / data-author / data-permalink / data-replies on the wrapper,
# a <span class="score unvoted" title="N"> for the score, and a <div class="md">...</div></form>
# carrying the markdown-rendered body.
NORMAL_COMMENT = """
<div class=" thing id-t1_normal noncollapsed comment " data-fullname="t1_normal"
     data-type="comment" data-author="alice" data-permalink="/r/SaaS/comments/abc/x/normal/"
     data-replies="2">
  <div class="entry">
    <span class="score dislikes" title="14">14 points</span>
    <span class="score unvoted" title="15">15 points</span>
    <span class="score likes" title="16">16 points</span>
    <form><div class="usertext-body"><div class="md"><p>Real talk: the MRR number isn't what
    impressed me. The fact that you shipped <strong>five distinct features</strong> in 90 days
    while keeping the team at two people is the actual story.</p></div></form>
  </div>
</div>
"""

AUTOMOD_COMMENT = """
<div class=" thing id-t1_automod comment " data-fullname="t1_automod"
     data-type="comment" data-author="AutoModerator" data-permalink="/r/SaaS/x/automod/"
     data-replies="0">
  <div><span class="score unvoted" title="1">1 point</span>
  <form><div class="usertext-body"><div class="md"><p>Reminder: please read the sub rules.</p></div></form></div>
</div>
"""

DELETED_AUTHOR_COMMENT = """
<div class=" thing id-t1_del comment " data-fullname="t1_del"
     data-type="comment" data-author="[deleted]" data-permalink="/r/x/del/" data-replies="0">
  <div><span class="score unvoted" title="3">3 points</span>
  <form><div class="usertext-body"><div class="md"><p>this should never surface</p></div></form></div>
</div>
"""

DEAD_BODY_COMMENT = """
<div class=" thing id-t1_dead comment " data-fullname="t1_dead"
     data-type="comment" data-author="ghoster" data-permalink="/r/x/dead/" data-replies="0">
  <div><span class="score unvoted" title="2">2 points</span>
  <form><div class="usertext-body"><div class="md"><p>[removed]</p></div></form></div>
</div>
"""

OP_REPLY_COMMENT = """
<div class=" thing id-t1_op comment " data-fullname="t1_op"
     data-type="comment" data-author="originaluser" data-permalink="/r/x/op/" data-replies="0">
  <div><span class="score unvoted" title="9">9 points</span>
  <form><div class="usertext-body"><div class="md"><p>OP here — happy to answer questions.</p></div></form></div>
</div>
"""

LONG_BODY_COMMENT = """
<div class=" thing id-t1_long comment " data-fullname="t1_long"
     data-type="comment" data-author="essayist" data-permalink="/r/x/long/" data-replies="0">
  <div><span class="score unvoted" title="42">42 points</span>
  <form><div class="usertext-body"><div class="md"><p>{LONG}</p></div></form></div>
</div>
""".replace("{LONG}", "lorem ipsum " * 200)  # ~2400 chars — well past the 800-char cap


def test_parse_normal_comment_all_fields():
    """🐞 Every field extracted: id (t1_ stripped), author, score, body (HTML→plain), is_op, replies."""
    comments = parse_old_reddit_comments(NORMAL_COMMENT, op_author=None, max_comments=5)
    assert len(comments) == 1, comments
    c = comments[0]
    assert c["id"] == "normal"
    assert c["fullname"] == "t1_normal"
    assert c["author"] == "alice"
    assert c["score"] == 15  # The 'unvoted' span — middle of the dislikes/unvoted/likes triplet
    assert "Real talk" in c["body"]
    assert "<strong>" not in c["body"], "HTML tags must be stripped"
    assert "five distinct features" in c["body"], "inline emphasis content preserved"
    assert c["is_op"] is False
    assert c["replies"] == 2
    print("✅ test_parse_normal_comment_all_fields")


def test_automoderator_filtered_out():
    """🐞 AutoModerator clutters every sub with reminders; the parser drops it before it reaches AI/System ②."""
    comments = parse_old_reddit_comments(NORMAL_COMMENT + AUTOMOD_COMMENT)
    ids = [c["id"] for c in comments]
    assert "automod" not in ids, ids
    assert "normal" in ids
    print("✅ test_automoderator_filtered_out")


def test_deleted_author_filtered_out():
    """🐞 author='[deleted]' / '[removed]' → drop (the content is gone or moderated)."""
    comments = parse_old_reddit_comments(NORMAL_COMMENT + DELETED_AUTHOR_COMMENT)
    assert all(c["author"] not in ("[deleted]", "[removed]") for c in comments), comments
    print("✅ test_deleted_author_filtered_out")


def test_dead_body_filtered_out():
    """🐞 body literally equal to '[deleted]' or '[removed]' → drop. Author may be alive but the text is dead."""
    comments = parse_old_reddit_comments(NORMAL_COMMENT + DEAD_BODY_COMMENT)
    ids = [c["id"] for c in comments]
    assert "dead" not in ids, ids
    print("✅ test_dead_body_filtered_out")


def test_op_detection():
    """🐞 When op_author is passed and matches the comment's author, is_op flag must be True."""
    comments = parse_old_reddit_comments(OP_REPLY_COMMENT, op_author="originaluser")
    assert len(comments) == 1
    assert comments[0]["is_op"] is True
    # Same fixture without op_author → False (defensive default).
    comments_no_op = parse_old_reddit_comments(OP_REPLY_COMMENT, op_author=None)
    assert comments_no_op[0]["is_op"] is False
    # Different author → False
    comments_other = parse_old_reddit_comments(OP_REPLY_COMMENT, op_author="someoneelse")
    assert comments_other[0]["is_op"] is False
    print("✅ test_op_detection")


def test_body_truncation_at_cap():
    """🐞 Bodies past _BODY_MAX_CHARS (800) get truncated with the "…(truncated)" marker.
    System ② readers must be able to tell at a glance that they're seeing a prefix, not the whole comment."""
    comments = parse_old_reddit_comments(LONG_BODY_COMMENT)
    assert len(comments) == 1
    body = comments[0]["body"]
    assert body.endswith("…(truncated)"), body[-30:]
    # The whole body string (prefix + marker) should be ≤ cap + len(marker); enforce a soft upper bound.
    assert len(body) <= 850, len(body)
    print("✅ test_body_truncation_at_cap")


def test_max_comments_caps_result_size():
    """🐞 Even if N>max_comments comments parse cleanly, only the top max_comments by score survive."""
    # Build 6 comments with descending scores 60, 50, ..., 10.
    fixtures = []
    for i, score in enumerate([60, 50, 40, 30, 20, 10]):
        fixtures.append(NORMAL_COMMENT
                        .replace("t1_normal", f"t1_n{i}")
                        .replace('title="14"', f'title="{score - 1}"')
                        .replace('title="15"', f'title="{score}"')
                        .replace('title="16"', f'title="{score + 1}"'))
    comments = parse_old_reddit_comments("".join(fixtures), max_comments=3)
    assert len(comments) == 3
    assert [c["score"] for c in comments] == [60, 50, 40], "must keep the top 3 by score, descending"
    print("✅ test_max_comments_caps_result_size")


def test_sort_by_score_descending():
    """🐞 Output order must be highest-score-first (so callers can take_first_N safely)."""
    # Two comments where the source HTML order is ascending; output should be descending.
    low_then_high = (NORMAL_COMMENT.replace("t1_normal", "t1_low").replace('title="15"', 'title="3"')
                     + NORMAL_COMMENT.replace("t1_normal", "t1_high").replace('title="15"', 'title="99"'))
    comments = parse_old_reddit_comments(low_then_high)
    assert [c["score"] for c in comments] == [99, 3], comments
    print("✅ test_sort_by_score_descending")


def test_empty_html_returns_empty_list():
    """🐞 Empty / no-comment HTML → empty list (caller skips silently)."""
    assert parse_old_reddit_comments("") == []
    assert parse_old_reddit_comments("<html><body>nothing</body></html>") == []
    print("✅ test_empty_html_returns_empty_list")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
