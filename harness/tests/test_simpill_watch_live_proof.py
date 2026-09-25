"""wq#2280 (wq#2265 leftover): the ONE live run of the wq#2242 PROOF shape ("a synthetic event
files exactly one ticket, a repeat files nothing" — see test_simpill_watch.py's docstring) against
the REAL `/home/lumen/bin/wq` CLI and a real network git clone of the configured fork, instead of
the FakeWq/stubbed-git doubles every other test in this suite uses. Closes the leftover from
wq#2265 (timer installed, fork_owner/us set, first run baselined 3 repos and filed nothing) that
the synthetic-fork PROOF itself was never exercised end to end.

Skipped by default: it needs network egress to github.com and spawns the real `wq` binary, which
a plain test run should not depend on. Set SIMPILL_LIVE_PROOF=1 to run it. `WQ_DB` points at a
scratch sqlite file for the run's duration, so this never lands a ticket in the production queue
— only the `wq add` call, the clone and the `fork` remote wiring are real.
"""
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import simpill_watch as sw  # noqa: E402

BIN_DIR = "/home/lumen/bin"
WQ_BIN = os.path.join(BIN_DIR, "wq")
MIGRATION = os.path.join(BIN_DIR, "migrations", "wq1331_needs.py")

pytestmark = pytest.mark.skipif(
    not os.environ.get("SIMPILL_LIVE_PROOF"),
    reason="opt-in: hits github.com and spawns the real wq CLI; set SIMPILL_LIVE_PROOF=1 to run")


def wq_list(lane):
    return subprocess.run([WQ_BIN, "list", "--lane", lane], capture_output=True, text=True,
                          timeout=30, check=True).stdout


def test_events_flag_files_one_ticket_against_the_real_fork_then_dedupes(tmp_path, monkeypatch):
    monkeypatch.setenv("WQ_DB", str(tmp_path / "scratch_wq.db"))
    monkeypatch.setenv("WQ_AGENT", "test-simpill-live-proof")
    subprocess.run([WQ_BIN, "list"], capture_output=True, text=True, timeout=30)  # inits the DB
    subprocess.run([sys.executable, MIGRATION, "--db", os.environ["WQ_DB"], "--yes"],
                   check=True, capture_output=True)
    monkeypatch.setattr(sw, "STATE", str(tmp_path / "state.json"))

    cfg = {"owner": "SkinnnyJay", "clone_root": str(tmp_path / "clones"),
           "fork_owner": "frankstupak", "us": ["frankstupak"], "repos": ["simpill-utils"]}
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps(cfg))
    events = tmp_path / "events.json"
    marker = "live-proof-1"

    events.write_text(json.dumps({"simpill-utils": []}))
    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    assert marker not in wq_list("simpill")

    events.write_text(json.dumps({"simpill-utils": [
        {"kind": "issue", "ident": marker, "title": "wq2280 live PROOF event",
         "url": "https://github.com/SkinnnyJay/simpill-utils",
         "summary": "wq2280 live proof run against a scratch WQ_DB, not a real upstream issue."}]}))
    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    filed_once = wq_list("simpill")
    assert filed_once.count(marker) == 1

    assert sw.main(["--config", str(cfgp), "--events", str(events)]) == 0
    assert wq_list("simpill") == filed_once  # repeat: same one ticket, no new row

    clone = tmp_path / "clones" / "simpill-utils"
    assert (clone / ".git").is_dir()
    remotes = subprocess.run(["git", "-C", str(clone), "remote"], capture_output=True, text=True,
                             timeout=30, check=True).stdout.split()
    assert sorted(remotes) == ["fork", "origin"]
