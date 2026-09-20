"""wq#2144: `wq add` (wq#2139) reads the LIVE foreman policy at git HEAD and refuses a lane that is
enabled for dispatch with no LANE_REPO row. These suites shell out to /home/lumen/bin/wq against a
scratch WQ_DB with invented lanes ('testlane', 'auto') to exercise the dispatcher, not the guard,
so point the guard at nothing: it fails open (one stderr warning) instead of depending on the
state of the real checkout. The guard's own behaviour is covered in /home/lumen/bin/tests/
test_wq_lane_repo_guard.py."""
import os

os.environ["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2144/router.py"
