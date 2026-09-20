"""dispatch_config_lint.validate_local_backends (wq#2130) + the committed config's own block."""
import json
import os
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, DISPATCHER_DIR)
import dispatch_config_lint as lint  # noqa: E402


def leaf(**kw):
    row = {"health_url": "http://h.test/api/ps", "slots": 2, "served_ctx": 32768,
           "first_frame_s": 1500}
    row.update(kw)
    return row


def check(block, **cfg):
    return lint.validate_local_backends({"local_backends": block, **cfg})


def test_absent_block_is_valid():
    assert lint.validate_local_backends({}) == ([], [])


def test_good_block_passes_and_notes_are_skipped():
    fails, warns = check({"_note": "x", "hathor-llama8b": leaf(),
                          "local-worker": {"group": ["hathor-llama8b"], "served_ctx": 32768,
                                           "first_frame_s": 1500}}, local_ceiling=12)
    assert (fails, warns) == ([], [])


def test_shipped_config_block_is_valid():
    cfg = json.load(open(os.path.join(DISPATCHER_DIR, "config.json")))
    assert cfg["local_backends"]
    assert lint.validate_local_backends(cfg) == ([], [])


def test_bad_leaf_fields_fail():
    fails, _ = check({"hathor-llama8b": leaf(slots=0, served_ctx="big", first_frame_s=None,
                                             health_url="hathor:11434")})
    assert len(fails) == 4
    assert any("slots" in f for f in fails) and any("health_url" in f for f in fails)


def test_backend_that_is_not_a_mac_local_model_fails():
    fails, _ = check({"claude-sonnet-5": leaf()})
    assert any("not a Mac-local model" in f for f in fails)
    fails, _ = check({"deepseek-v4-flash": leaf()})       # cheap_cloud has no Mac slots
    assert any("not a Mac-local model" in f for f in fails)


def test_group_members_must_be_leaf_rows_and_carry_no_slots():
    fails, _ = check({"hathor-llama8b": leaf(),
                      "local-worker": {"group": ["hathor-llama8b", "ghost"], "slots": 4}})
    assert any("ghost" in f for f in fails) and any("takes no slots" in f for f in fails)
    fails, _ = check({"local-worker": {"group": []}})
    assert any("non-empty list" in f for f in fails)


def test_non_object_block_and_rows_fail():
    assert check([])[0] == ["local_backends must be an object"]
    assert any("must be an object" in f for f in check({"hathor-llama8b": 3})[0])


def test_slots_above_local_ceiling_warn():
    fails, warns = check({"hathor-llama8b": leaf(slots=8), "nephthys-qwen14b": leaf(slots=8)},
                         local_ceiling=12)
    assert fails == [] and len(warns) == 1 and "clamps" in warns[0]
