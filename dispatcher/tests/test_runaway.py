"""runaway.py — runaway-ticket detector unit tests (wq#718 AUTO-6).

Zero quota: the real `wq` binary runs against a scratch WQ_DB sqlite file (same fixture
pattern as test_self_heal.py); attempt-N.json fixtures are hand-written stubs (runaway
only reads self_heal.attempt_count()'s file COUNT, never their contents).
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DISPATCHER_DIR = os.path.dirname(TESTS_DIR)
REPO = os.path.dirname(DISPATCHER_DIR)
sys.path.insert(0, DISPATCHER_DIR)
sys.path.insert(0, os.path.join(REPO, "harness"))
import runaway     # noqa: E402
import self_heal   # noqa: E402
import capability_lint  # noqa: E402

WQ_BIN = "/home/lumen/bin/wq"


@pytest.fixture
def renv(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    wq_db = tmp_path / "scratch_wq.db"
    # wq#912: the real `wq` binary refuses `add` for a lane with no dispatcher/config.json
    # entry. Point it at a scratch config carrying just the fixture's own lane so the real
    # CLI's own safety check never depends on the live fleet config.
    dispatcher_config = tmp_path / "dispatcher_config.json"
    dispatcher_config.write_text('{"lanes": {"testlane": {}}}')

    monkeypatch.setenv("WQ_DB", str(wq_db))
    monkeypatch.setenv("WQ_AGENT", "test-runaway")
    monkeypatch.setenv("WQ_DISPATCHER_CONFIG", str(dispatcher_config))
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(self_heal, "WQ_BIN", WQ_BIN)
    monkeypatch.setattr(runaway, "RUNAWAY_MAX_ATTEMPTS", 5)

    return {"tmp": tmp_path, "runs_root": runs_root, "wq_db": str(wq_db)}


def add_ticket(title="scratch runaway ticket", lane="testlane"):
    p = subprocess.run([WQ_BIN, "add", "--lane", lane, "--title", title],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def set_status(tid, status):
    """Force a ticket's status without going through claim/block (runaway.py only
    cares about the current status column, not how it got there)."""
    db = os.environ["WQ_DB"]
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, tid))
        conn.commit()
    finally:
        conn.close()


def make_attempts(renv, tid, n):
    d = renv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (d / f"attempt-{i}.json").write_text("{}")


# ---- candidate_ticket_ids ----------------------------------------------------------------

def test_candidate_ticket_ids_lists_every_run_dir(renv):
    make_attempts(renv, 1, 2)
    make_attempts(renv, 2, 1)
    (renv["runs_root"] / "not-a-ticket-dir").mkdir()

    assert sorted(runaway.candidate_ticket_ids()) == [1, 2]


def test_candidate_ticket_ids_empty_when_runs_root_missing(renv, monkeypatch):
    monkeypatch.setattr(self_heal, "RUNS_ROOT", str(renv["tmp"] / "does-not-exist"))
    assert runaway.candidate_ticket_ids() == []


# ---- stuck_ticket_ids ----------------------------------------------------------------

def test_below_threshold_not_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 4)  # < RUNAWAY_MAX_ATTEMPTS=5

    assert runaway.stuck_ticket_ids() == {}


def test_at_threshold_and_not_done_is_stuck(renv):
    tid = add_ticket(lane="testlane")
    make_attempts(renv, tid, 5)

    stuck = runaway.stuck_ticket_ids()
    assert set(stuck) == {tid}
    assert stuck[tid]["attempts"] == 5
    assert stuck[tid]["status"] == "open"
    assert stuck[tid]["lane"] == "testlane"


def test_above_threshold_still_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 9)

    assert set(runaway.stuck_ticket_ids()) == {tid}


def test_done_ticket_never_stuck_even_at_high_attempt_count(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 8)
    set_status(tid, "done")

    assert runaway.stuck_ticket_ids() == {}


def test_blocked_ticket_at_threshold_is_stuck(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 5)
    set_status(tid, "blocked")

    stuck = runaway.stuck_ticket_ids()
    assert stuck[tid]["status"] == "blocked"


def test_custom_threshold_override(renv):
    tid = add_ticket()
    make_attempts(renv, tid, 2)

    assert runaway.stuck_ticket_ids(threshold=2) == {
        tid: {"attempts": 2, "status": "open", "lane": "testlane"}}
    assert runaway.stuck_ticket_ids(threshold=3) == {}


def test_multiple_tickets_mixed(renv):
    stuck_tid = add_ticket("stuck one")
    make_attempts(renv, stuck_tid, 6)
    fine_tid = add_ticket("fine one")
    make_attempts(renv, fine_tid, 1)
    done_tid = add_ticket("done one")
    make_attempts(renv, done_tid, 7)
    set_status(done_tid, "done")

    assert set(runaway.stuck_ticket_ids()) == {stuck_tid}


# ---- CLI ---------------------------------------------------------------------------------

def test_cmd_list_stuck(renv, capsys):
    tid = add_ticket()
    make_attempts(renv, tid, 5)

    assert runaway.main(["list-stuck"]) == 0
    out = capsys.readouterr().out
    assert f'"{tid}"' in out


# ---- wq#1438 §2.2: harness-defect hold release --------------------------------------
#
# Test-to-failure row (vii): a ticket held on CITED_PATH_UNREACHABLE must stop being
# held once the paths that refusal named are inside its effective add_dirs — otherwise
# auto-tagging a held ticket never lets it run.

@pytest.fixture(autouse=True)
def _hermetic_lint(monkeypatch):
    """hold_released() runs the lint; never let a unit test read the live fleet."""
    monkeypatch.setattr(capability_lint, "load_roster",
                        lambda refresh=False: capability_lint.Roster(names=["lumen"]))


def write_failure(renv, tid, **fields):
    d = renv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"ticket": tid, "triage": "HARNESS_DEFECT",
           "summary": "CITED_PATH_UNREACHABLE"}
    rec.update(fields)
    (d / "failure.json").write_text(json.dumps(rec))


def set_needs(tid, needs):
    """Write the `needs` column, adding it first if this scratch schema predates the
    wq#1331 migration — a fresh `wq add` DB does not carry it, only the live queue and
    anything migrated does (`wq edit --needs` refuses outright without it)."""
    conn = sqlite3.connect(os.environ["WQ_DB"])
    try:
        try:
            conn.execute("ALTER TABLE tasks ADD COLUMN needs TEXT")
        except sqlite3.OperationalError:
            pass  # already there
        conn.execute("UPDATE tasks SET needs=? WHERE id=?", (needs, tid))
        conn.commit()
    finally:
        conn.close()


def test_hold_stands_while_the_cited_path_is_still_unreachable(renv):
    tid = add_ticket("cites a path nothing grants")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/nowhere-at-all"])

    assert tid in runaway.harness_defect_ticket_ids()


def test_hold_releases_once_the_ticket_carries_the_dir_tag(renv):
    """The auto-tag path: stage 3.7 wrote needs:dir(...), so the next spawn clears
    preflight and the hold is just a loop that never ends."""
    # title cites nothing, so the lint derives nothing: only the written tag can
    # release this hold.
    tid = add_ticket("the worker could not reach its notes")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/agent/notes.md"])
    assert tid in runaway.harness_defect_ticket_ids()

    set_needs(tid, "dir(/home/lumen/agent)")
    assert tid not in runaway.harness_defect_ticket_ids()


def test_hold_releases_on_a_tag_the_lint_derives_this_pass(renv):
    """Even before the write lands, the lint's own derivation covers the path — the
    hold and the pre-skip must agree about what the next spawn would reach."""
    tid = add_ticket("ops: read /home/lumen/agent for the config")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/agent"])

    assert tid not in runaway.harness_defect_ticket_ids()


def test_partial_coverage_keeps_the_hold(renv):
    tid = add_ticket("two paths, one granted")
    write_failure(renv, tid,
                  unreachable_paths=["/home/lumen/agent", "/home/lumen/nowhere-at-all"])
    set_needs(tid, "dir(/home/lumen/agent)")

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_v1_record_with_no_structured_paths_keeps_its_hold(renv):
    """Pre-slice-A failure.json always wrote unreachable_paths: [] (§2.5). An empty
    list is "we don't know", and a hold must never release on not knowing."""
    tid = add_ticket("old record, no structured fields")
    set_needs(tid, "dir(/home/lumen/agent)")
    write_failure(renv, tid)  # no unreachable_paths key at all

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_non_path_defect_keeps_its_hold(renv):
    """WORKTREE_REPO_MISMATCH and hook crashes are what the backstop is FOR — only a
    defect this module can prove is fixed gets to bypass it."""
    tid = add_ticket("worktree/repo mismatch")
    set_needs(tid, "dir(/home/lumen/agent)")
    write_failure(renv, tid, summary="WORKTREE_REPO_MISMATCH")

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_lint_crash_never_releases_a_hold(renv, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("lint boom")
    monkeypatch.setattr(capability_lint, "derive", boom)
    tid = add_ticket("reads /home/lumen/agent")
    set_needs(tid, "dir(/home/lumen/agent)")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/agent"])

    assert tid in runaway.harness_defect_ticket_ids()
