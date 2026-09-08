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


def fresh_sweep():
    """wq#1482: a fresh, hitless read_dir_sweep.json — this file's corpus is about
    the allow-list gate (wq#1441), not the sweep gate, so replay() should see the
    same "sweep just ran clean" state `foreman_dispatcher` sees once the timer has
    run at least once."""
    import datetime
    policy = capability_lint.load_policy()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 0, "hits": []}
             for r in (policy.get("allow") or {}).get("read_dirs") or []}
    return {"ts": ts, "roots": roots}

# The four tickets' real shapes: a cited path outside every add_dirs, and — for 1401
# only — a v2 verb in the same detail. Text trimmed to the parts stage 3.7 reads.
CORPUS = [
    (1401, "Maintenance-window scheduler",
     "doc /home/lumen/instructions/playbooks/maintenance-window.md ... and REBOOTS "
     "only if boot-safety triple-checked; reboot the node afterwards",
     "needs_unresolved"),
    # wq#1441: deny is the default. /home/lumen/agent carries live tokens (agent/.env
    # + .env.bak.*), backups/ and .npm-global/ are not swept read roots — all three
    # now land in needs_unresolved with the propose-by-PR hint instead of an auto
    # grant. A cited path UNDER allow.read_dirs still lands dispatchable (last row).
    (1402, "agent notes", "read the config under /home/lumen/agent", "needs_unresolved"),
    (1403, "wq proposal backups",
     "write to /home/lumen/backups/wq-proposals nightly", "needs_unresolved"),
    (1407, "Fix preflight claude spawn under systemd PATH",
     "PATH lacks /home/lumen/.npm-global/bin so every auto-reprove fails",
     "needs_unresolved"),
    (9999, "allowed read root",
     "read /home/lumen/research_prompts and summarise", "dispatchable"),
]


@pytest.fixture(autouse=True)
def _apply_dir(monkeypatch):
    monkeypatch.setenv("FOREMAN_NEEDS_LINT_MODE", "apply-dir")


@pytest.mark.parametrize("tid,title,detail,column", CORPUS,
                         ids=[str(c[0]) for c in CORPUS])
def test_each_ticket_lands_in_the_predicted_column(tid, title, detail, column):
    t = {"id": tid, "title": title, "detail": detail, "needs": None,
         "archetype": "standard-engineering", "status": "blocked"}
    r = capability_replay.replay(t, repo="/home/lumen/foreman", roster=ROSTER,
                                 sweep=fresh_sweep())
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
                                 repo="/home/lumen/foreman", roster=ROSTER,
                                 sweep=fresh_sweep())
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
    """The drill on the real rows. Under wq#1441's explicit allow list none of the
    four gets an auto grant: 1401 is held by its v2 verb, 1402/1403/1407 cite dirs
    that are not swept read roots. Nothing is spawned and nothing is written — this
    module has no code path that can do either."""
    tickets = capability_replay.load_tickets([1401, 1402, 1403, 1407], LIVE_DB)
    assert {t["id"] for t in tickets} == {1401, 1402, 1403, 1407}
    got = {t["id"]: capability_replay.replay(t) for t in tickets}
    for tid in (1401, 1402, 1403, 1407):
        assert got[tid]["column"] == "needs_unresolved", (tid, got[tid])
        assert got[tid].get("apply", []) == []
    assert any("allow.read_dirs" in u for u in got[1402]["unresolved"])
