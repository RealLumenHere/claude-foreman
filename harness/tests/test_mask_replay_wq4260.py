"""wq#4261's verdict on wq#4260's kv-secret alarm, pinned (fixtures/wq4260_values.py).

Kept out of test_mask_replay.py for the same reason as test_mask_replay_wq3753.py.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import mask_replay  # noqa: E402
import masking  # noqa: E402
from test_mask_replay import _assert_corpus_pinned, _load_fixture_module, _unpinned  # noqa: E402

WQ4260_RUN = os.path.join(mask_replay.RUNS_ROOT, "wq4260")
WQ4260_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "wq4260_values.py")


def test_wq4260_pins_are_their_hashes_and_read_silently():
    text, scope = _load_fixture_module(WQ4260_FIXTURES)
    values = scope["WQ4260_VALUES"]
    count = len(values)
    assert count == 2
    assert _unpinned(values, {"f": text}) == []
    assert masking.mask_text(text)[1] == []
    assert [r[0] for r in masking._orphan_matches(text)] == []


@pytest.mark.skipif(not os.path.isdir(WQ4260_RUN),
                    reason="wq#4260's run dir is not on this host")
def test_wq4260_corpus_burned_nothing():
    """wq#4260 typed two self-labelled dummies after `ANTHROPIC_API_KEY=` into its own
    install.sh test inputs; not credentials, nothing rotated."""
    _, scope = _load_fixture_module(WQ4260_FIXTURES)
    fixture_shas = {v[0] for v in scope["WQ4260_VALUES"]}
    _assert_corpus_pinned(4260, WQ4260_FIXTURES, scope["WQ4260_RECORDS"], fixture_shas,
                          patterns=mask_replay.CURRENT_PATTERNS)
