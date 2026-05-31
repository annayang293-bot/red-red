"""run_once exit-code policy — locks in the rule that degraded runs (total upstream outage or
zero-scored due to source failure) exit 1 so GH Actions surfaces them as red.

Background: run 52 wrote a "success" workflow_run despite Reddit returning 403 on all 6
subreddits, because run_once's old policy was "exit 0 if Supabase save succeeded." That hid the
problem. The new policy is encoded in `_is_degraded`; this test pins the contract.

Run: python3 system1-app/pipeline/tests/test_run_once_exit.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from pipeline.run_once import _is_degraded  # noqa: E402


def test_all_good_exits_clean():
    # All sources up, 78 hot items scored — the run-51 happy path.
    assert _is_degraded(failed_sources=[], scored_count=78, source_count=2) is False
    print("✅ test_all_good_exits_clean")


def test_quiet_day_still_clean():
    # No source failures, just nothing newsworthy passed the gates. Don't cry wolf.
    assert _is_degraded(failed_sources=[], scored_count=0, source_count=2) is False
    print("✅ test_quiet_day_still_clean")


def test_all_sources_failed_is_degraded():
    # Reddit + PH both threw → both names in failed_sources → degraded.
    assert _is_degraded(
        failed_sources=["reddit", "product_hunt"], scored_count=0, source_count=2,
    ) is True
    print("✅ test_all_sources_failed_is_degraded")


def test_run52_pattern_is_degraded():
    # Reddit reached but all subs 403'd (one entry in failed_sources), PH quota produced a
    # couple of zero-engagement items so scored_count is 0. Exactly the run-52 production
    # incident that motivated this rule.
    assert _is_degraded(
        failed_sources=["reddit:OpenAI,SaaS,Entrepreneur,startups,indiehackers,artificial"],
        scored_count=0,
        source_count=2,
    ) is True
    print("✅ test_run52_pattern_is_degraded")


def test_partial_failure_with_useful_posts_is_clean():
    # PH up, Reddit partially failed (some subs OK), still got 30 hot items past the gate.
    # The run is good enough; don't flag.
    assert _is_degraded(
        failed_sources=["reddit:OpenAI"], scored_count=30, source_count=2,
    ) is False
    print("✅ test_partial_failure_with_useful_posts_is_clean")


def test_zero_sources_never_degraded():
    # Defensive: a config with no sources at all (shouldn't happen in prod) doesn't trigger
    # the "all sources failed" branch — `source_count > 0` guard keeps the rule meaningful.
    assert _is_degraded(failed_sources=[], scored_count=0, source_count=0) is False
    print("✅ test_zero_sources_never_degraded")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"\nALL {len(fns)} TESTS PASSED ✅")
