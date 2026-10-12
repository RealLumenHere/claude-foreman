"""wq#3765's verdict on wq#3753's kv-secret alarm, pinned (fixtures/wq3753_values.py).

Kept out of test_mask_replay.py, whose primary-checkout copy carries wq#3481's
uncommitted re-pin; lumen-pr refuses to land any PR touching that path until it does.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay  # noqa: E402
import masking  # noqa: E402
from test_mask_replay import _assert_corpus_pinned, _load_fixture_module, _unpinned  # noqa: E402

WQ3753_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq3753")
WQ3753_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq3753_values.py")


def test_wq3753_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ3753_FIXTURES)
    values = scope["WQ3753_VALUES"]
    count = len(values)
    assert count == 1
    assert _unpinned(values, {"f": text}) == []
    assert masking.mask_text(text)[1] == []
    assert [r[0] for r in masking._orphan_matches(text)] == []


@pytest.mark.skipif(not os.path.isdir(WQ3753_RUN),
                    reason="wq#3753's run dir is not on this host")
def test_wq3753_corpus_burned_nothing():
    """wq#3753 read board prose `NOT A TOKEN: <vendored source path>` and the kv rule
    took the path for a value; not a credential, nothing rotated."""
    _, scope = _load_fixture_module(WQ3753_FIXTURES)
    fixture_shas = {v[0] for v in scope["WQ3753_VALUES"]}
    _assert_corpus_pinned(3753, WQ3753_FIXTURES, scope["WQ3753_RECORDS"], fixture_shas,
                          patterns=mask_replay.CURRENT_PATTERNS)
