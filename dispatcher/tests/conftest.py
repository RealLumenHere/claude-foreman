"""wq#2144: `wq add` (wq#2139) reads the LIVE foreman policy at git HEAD and refuses a lane that is
enabled for dispatch with no LANE_REPO row. These suites shell out to /home/lumen/bin/wq against a
scratch WQ_DB with invented lanes ('testlane', 'auto') to exercise the dispatcher, not the guard,
so point the guard at nothing: it fails open (one stderr warning) instead of depending on the
state of the real checkout. The guard's own behaviour is covered in /home/lumen/bin/tests/
test_wq_lane_repo_guard.py.

wq#2134: gate_store parks tickets in the wq db named by WQ_DB. A test that runs the gate pipeline
without its own scratch WQ_DB would fingerprint and park real ticket ids in the LIVE queue.db, so
every test starts with WQ_DB pointing at an empty per-test file (fixtures that build a real
scratch db, like `denv`, override it)."""
import os

import pytest

os.environ["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2144/router.py"


@pytest.fixture(autouse=True)
def _scratch_wq_db(tmp_path_factory, monkeypatch):
    if "WQ_DB" not in os.environ:
        monkeypatch.setenv("WQ_DB", str(tmp_path_factory.mktemp("wqdb") / "empty_wq.db"))
