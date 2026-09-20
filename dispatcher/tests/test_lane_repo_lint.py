"""dispatch_config_lint.validate_lane_repos (wq#2133) + the shipped config's own lanes."""
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_config_lint as lint  # noqa: E402


def lanes(**kw):
    return {"lanes": kw}


def test_enabled_lane_without_a_repo_row_fails():
    fails = lint.validate_lane_repos(lanes(simpill={"enabled": True, "max_per_day": 999}))
    assert len(fails) == 1
    assert "'simpill'" in fails[0] and "LANE_REPO" in fails[0]


def test_disabled_lane_without_a_row_is_fine():
    assert lint.validate_lane_repos(lanes(simpill={"enabled": False})) == []


def test_mapped_lane_is_fine():
    assert lint.validate_lane_repos(lanes(build={"enabled": True}, pstack={"enabled": True})) == []


def test_human_lanes_and_the_repo_hint_lane_are_exempt():
    cfg = lanes(**{name: {"enabled": True} for name in
                   ("user", "user-projects", "frank-re", "frank-hw", "frank-funding",
                    "frank-cred", "auto")})
    assert lint.validate_lane_repos(cfg) == []


def test_only_a_real_true_counts_as_enabled():
    cfg = lanes(a={"enabled": "yes"}, b={"enabled": 1}, c={}, d="oops")
    assert lint.validate_lane_repos(cfg) == []


def test_missing_or_malformed_lanes_block_is_not_a_repo_defect():
    assert lint.validate_lane_repos({}) == []
    assert lint.validate_lane_repos({"lanes": []}) == []


def test_every_unmapped_enabled_lane_is_listed():
    fails = lint.validate_lane_repos(lanes(x1={"enabled": True}, x2={"enabled": True}))
    assert [f.split("'")[1] for f in fails] == ["x1", "x2"]


def test_shipped_config_has_no_enabled_lane_without_a_repo():
    with open(os.path.join(DISPATCHER_DIR, "config.json")) as f:
        cfg = json.load(f)
    assert lint.validate_lane_repos(cfg) == []
    assert cfg["lanes"]["simpill"]["enabled"] is True
    assert cfg["lanes"]["simpill"]["multi_repo"] is True


def test_multi_repo_lane_is_exempt_only_with_a_real_true():
    assert lint.validate_lane_repos(lanes(simpill={"enabled": True, "multi_repo": True})) == []
    fails = lint.validate_lane_repos(lanes(simpill={"enabled": True, "multi_repo": "yes"}))
    assert len(fails) == 1 and "'simpill'" in fails[0]
