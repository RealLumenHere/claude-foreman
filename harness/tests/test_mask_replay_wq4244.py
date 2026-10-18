"""wq#4251's verdict on wq#4244's kv-secret alarm, pinned (fixtures/wq4244_values.py).

Kept out of test_mask_replay.py for the same reason as test_mask_replay_wq3753.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay  # noqa: E402
import masking  # noqa: E402
from test_mask_replay import _assert_corpus_pinned, _load_fixture_module, _unpinned  # noqa: E402

WQ4244_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq4244")
WQ4244_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq4244_values.py")


def test_wq4244_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ4244_FIXTURES)
    values = scope["WQ4244_VALUES"]
    count = len(values)
    assert count == 1
    assert _unpinned(values, {"f": text}) == []
    assert masking.mask_text(text)[1] == []
    assert [r[0] for r in masking._orphan_matches(text)] == []


@pytest.mark.skipif(not os.path.isdir(WQ4244_RUN),
                    reason="wq#4244's run dir is not on this host")
def test_wq4244_corpus_burned_nothing():
    """wq#4244 ran `wq show 4237`, whose detail quotes an unbracketed config
    placeholder after `OPENAI_API_KEY=`; not a credential, nothing rotated."""
    _, scope = _load_fixture_module(WQ4244_FIXTURES)
    fixture_shas = {v[0] for v in scope["WQ4244_VALUES"]}
    _assert_corpus_pinned(4244, WQ4244_FIXTURES, scope["WQ4244_RECORDS"], fixture_shas,
                          patterns=mask_replay.CURRENT_PATTERNS)
