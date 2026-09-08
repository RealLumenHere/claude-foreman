"""capability_replay.py — BROKER_WIRING.md §4 row (iv), live drill half (wq#1438).

The row asks for "a --dry-run replay of wq#1401/1402/1403/1407 landing in the predicted
column with zero spawns". All four are `blocked`, so a real dispatcher pass refuses them
at `not_open` before stage 3.7 is reached — the replay harness asks stage 3.7 directly.

Two layers here. The synthetic rows carry the same shape as the real four and pin the
predicted column deterministically (no queue, no fleet). The live-queue test is the
drill itself, skipped when the queue is absent so this file still runs anywhere.
"""
import os
import sys
import time

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import capability_lint  # noqa: E402
import capability_replay  # noqa: E402

LIVE_DB = "/home/lumen/agents/work-queue/queue.db"
ROSTER = capability_lint.Roster(names=["lumen", "isis", "thor"], ips={})

# The four tickets' real shapes: a cited path outside every add_dirs, and — for 1401
# only — a v2 verb in the same detail. Text trimmed to the parts stage 3.7 reads.
#
# wq#1441 (BROKER_GRANT_COVERAGE.md §2, slice 1) landed AFTER this doc's original
# "1402/1403/1407 -> auto dir -> dispatchable" prediction and made dir(X) explicit-
# allow-only: none of /home/lumen/agent, /home/lumen/backups/wq-proposals, or
# /home/lumen/.npm-global is on the curated fleet-exec-policy.yaml allow.read_dirs
# list (agent carries live tokens — the wq#1466 class this same slice exists to
# catch). All three now land in needs_unresolved with the propose-don't-deploy hint,
# same as 1401 — this is the intended tightening, not a regression.
CORPUS = [
    (1401, "Maintenance-window scheduler",
     "doc /home/lumen/instructions/playbooks/maintenance-window.md ... and REBOOTS "
     "only if boot-safety triple-checked; reboot the node afterwards",
     "needs_unresolved"),
    (1402, "agent notes", "read the config under /home/lumen/agent", "needs_unresolved"),
    (1403, "wq proposal backups",
     "write to /home/lumen/backups/wq-proposals nightly", "needs_unresolved"),
    (1407, "Fix preflight claude spawn under systemd PATH",
     "PATH lacks /home/lumen/.npm-global/bin so every auto-reprove fails",
     "needs_unresolved"),
]


@pytest.fixture(autouse=True)
def _apply_dir(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")
    # wq#1482 slice 5: this corpus predates the sweep gate and is about which column a
    # ticket lands in, not sweep freshness — fixture-in a fresh no-hit sweep so a
    # missing runtime/read_dir_sweep.json doesn't turn every predicted dir tag into
    # needs_unresolved here.
    monkeypatch.setattr(capability_lint, "load_sweep",
                        lambda path=None: {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                           "roots": {}})


@pytest.mark.parametrize("tid,title,detail,column", CORPUS,
                         ids=[str(c[0]) for c in CORPUS])
def test_each_ticket_lands_in_the_predicted_column(tid, title, detail, column):
    t = {"id": tid, "title": title, "detail": detail, "needs": None,
         "archetype": "standard-engineering", "status": "blocked"}
    r = capability_replay.replay(t, repo="/home/lumen/foreman", roster=ROSTER)
    assert r["column"] == column
    if column == "dispatchable":
        # the dir tag is what makes the path reachable; the replay names what the
        # dispatcher WOULD write, and writes nothing itself.
        assert r["apply"] and all(a.startswith("dir(") for a in r["apply"])


def test_1401_names_the_v2_verb_that_holds_it():
    """The doc's §4 row-iv prediction ("1401 -> auto dir") predates the lint's v2-verb
    bucket. §2.4 is the governing text: a ticket that needs `reboot` stays
    needs_unresolved with the missing verb named, "which is the honest state". The dir
    tag is still derived — it is simply not applied to a ticket being skipped."""
    tid, title, detail, _ = CORPUS[0]
    r = capability_replay.replay({"id": tid, "title": title, "detail": detail,
                                  "needs": None, "archetype": "standard-engineering"},
                                 repo="/home/lumen/foreman", roster=ROSTER)
    assert r["column"] == "needs_unresolved" and "reboot" in r["detail"]
    assert r["tags"] == ["dir(/home/lumen/instructions/playbooks)"]
    assert "apply" not in r


def test_the_replay_never_writes(monkeypatch):
    """No third writer: the dispatcher owns the `needs` column (§2.1/§3)."""
    import capability
    monkeypatch.setattr(capability, "_wq",
                        lambda *a: pytest.fail(f"replay shelled out to wq {a}"))
    for tid, title, detail, _ in CORPUS:
        capability_replay.replay({"id": tid, "title": title, "detail": detail,
                                  "needs": None, "archetype": None},
                                 repo="/home/lumen/foreman", roster=ROSTER)


@pytest.mark.skipif(not os.path.exists(LIVE_DB), reason="no live queue on this host")
def test_live_replay_of_the_four_stuck_tickets():
    """The drill on the real rows. wq#1441 slice 1 tightened dir(X) to explicit-allow-
    only after this doc's original prediction, and none of 1402/1403/1407's cited
    dirs made the curated allow.read_dirs list — all four now land in
    needs_unresolved (1401 on its v2 verb, the rest on the allow-hint), which is the
    intended tightening (see the CORPUS comment above). Nothing is spawned and
    nothing is written — this module has no code path that can do either."""
    tickets = capability_replay.load_tickets([1401, 1402, 1403, 1407], LIVE_DB)
    assert {t["id"] for t in tickets} == {1401, 1402, 1403, 1407}
    got = {t["id"]: capability_replay.replay(t) for t in tickets}
    for tid in (1401, 1402, 1403, 1407):
        assert got[tid]["column"] == "needs_unresolved", (tid, got[tid])
