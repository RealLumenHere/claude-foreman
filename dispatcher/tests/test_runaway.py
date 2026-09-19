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

    # wq#1671: `wq init()` creates every column EXCEPT lease_owner/lease_expires_at/fence/
    # attempt — those are gated behind the wq#1158 migration (bin/wq: LEASE_COLS,
    # lease_fenced()) so an unmigrated DB keeps wq's pre-fence claim behaviour instead of
    # silently changing the claim protocol underneath live sessions. Production HAS this
    # migration applied (that's the only reason wq#1671's own evidence — e.g. wq#1961's
    # attempt=10 — exists at all), so a scratch DB that skips it is not representative of
    # what stuck_ticket_ids() actually runs against. Force one `wq add` first so init()
    # creates the tasks table, then apply the real migration script.
    add_ticket("wq#1158 migration seed — deleted immediately", lane="testlane")
    mig = subprocess.run(
        [sys.executable, "/home/lumen/bin/migrations/wq1158_lease_fence.py",
         "--db", str(wq_db), "--yes"],
        capture_output=True, text=True)
    assert mig.returncode == 0, mig.stdout + mig.stderr
    conn = sqlite3.connect(str(wq_db))
    try:
        conn.execute("DELETE FROM tasks")  # clear the seed row; real tests add their own
        conn.commit()
    finally:
        conn.close()

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


def test_stuck_scan_reads_wq_once_not_per_run_dir(renv, monkeypatch):
    """wq#2082 (research/DISPATCH_STALL_RCA.md §3.4): one `wq show` subprocess per
    RUNS_ROOT dir was 171s of a 197s pass on 1052 dirs. The scan must get every wq
    column from a single batched read — no per-ticket subprocess at all — and still
    catch both stuck shapes (file count, wq `attempt` column) and spare done tickets."""
    def no_subprocess(tid):
        raise AssertionError(f"wq_show({tid}) called: per-ticket subprocess is back")
    monkeypatch.setattr(self_heal, "wq_show", no_subprocess)

    by_files = add_ticket("stuck by attempt files", lane="testlane")
    make_attempts(renv, by_files, 5)
    by_column = add_ticket("stuck by wq attempt column", lane="testlane")
    make_attempts(renv, by_column, 1)
    db = sqlite3.connect(os.environ["WQ_DB"])
    try:
        db.execute("UPDATE tasks SET attempt=7 WHERE id=?", (by_column,))
        db.commit()
    finally:
        db.close()
    under = add_ticket("under threshold")
    make_attempts(renv, under, 2)
    done = add_ticket("done despite many attempts")
    make_attempts(renv, done, 9)
    set_status(done, "done")

    stuck = runaway.stuck_ticket_ids()
    assert set(stuck) == {by_files, by_column}
    assert stuck[by_files] == {"attempts": 5, "status": "open", "lane": "testlane"}
    assert stuck[by_column] == {"attempts": 7, "status": "open", "lane": "testlane"}


def test_wq_rows_falls_back_to_wq_show_when_db_unreadable(renv, monkeypatch):
    """The batched read must never make the answer worse than before: an unreadable
    WQ_DB degrades to the old per-id wq_show path, not to 'nothing is stuck'."""
    monkeypatch.setenv("WQ_DB", str(renv["tmp"] / "missing.db"))
    calls = []

    def fake_show(tid):
        calls.append(tid)
        return {"attempt": 6, "status": "open", "lane": "testlane"} if tid == 41 else {}
    monkeypatch.setattr(self_heal, "wq_show", fake_show)

    rows = self_heal.wq_rows([41, 42])
    assert rows == {41: {"attempt": 6, "status": "open", "lane": "testlane"}}
    assert sorted(calls) == [41, 42]


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


# ---- wq#1671: crash-before-charge -- the wq `attempt` DB column as a second signal -------

def bump_db_attempt(tid, n):
    """Set the wq row's own `attempt` column directly -- what `wq claim` increments on
    every successful claim (bin/wq: COALESCE(attempt,0)+1), independent of whatever the
    claimed process goes on to write under RUNS_ROOT. Tests use this instead of actually
    claiming n times so they don't need a live fworker to reproduce the gap."""
    db = os.environ["WQ_DB"]
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE tasks SET attempt=? WHERE id=?", (n, tid))
        conn.commit()
    finally:
        conn.close()


def test_db_attempt_column_catches_a_claim_that_never_wrote_a_file(renv):
    """The class this ticket exists for: a ticket claimed 5 times, each claim dying
    before write_attempt_state ever ran (unhandled exception, or a hard external kill
    no except block can catch). attempt_count() alone sees zero files and never trips --
    the wq column is the only thing that saw the 5 claims happen at all."""
    tid = add_ticket()
    d = renv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)  # run_dir() is created on claim; no attempt-N.json inside
    bump_db_attempt(tid, 5)

    assert self_heal.attempt_count(tid) == 0  # the blind spot, confirmed
    stuck = runaway.stuck_ticket_ids()
    assert set(stuck) == {tid}
    assert stuck[tid]["attempts"] == 5


def test_db_attempt_below_threshold_alone_does_not_trip(renv):
    tid = add_ticket()
    d = renv["runs_root"] / f"wq{tid}"
    d.mkdir(parents=True, exist_ok=True)
    bump_db_attempt(tid, 4)  # < RUNAWAY_MAX_ATTEMPTS=5

    assert runaway.stuck_ticket_ids() == {}


def test_effective_attempts_is_the_max_not_the_sum(renv):
    """A ticket claimed once (db attempt=1) that then ran 6 real attempt-N.json rounds
    inside that one claim (e.g. handle_resumable_failure's retry ladder) is a real 6-try
    runaway on the file count alone -- summing the two counters (1+6=7) would still call
    it stuck here, so this doesn't distinguish max from sum. The case that DOES: db
    climbs past threshold on repeated crash-before-charge claims while a stray single
    file sits in the run dir from one attempt that DID get far enough to write it. Sum
    would double-count that lone file on top of every claim; max must not."""
    tid = add_ticket()
    make_attempts(renv, tid, 1)
    bump_db_attempt(tid, 5)

    stuck = runaway.stuck_ticket_ids()
    assert set(stuck) == {tid}
    assert stuck[tid]["attempts"] == 5  # max(1, 5), not 1+5=6


def test_file_count_above_threshold_still_trips_even_if_db_column_is_low(renv):
    """A resumed session can legitimately run several attempt-N.json rounds under ONE
    wq claim (db attempt stays low) -- the pre-existing file-based path must keep
    catching that shape; the new db check is additive, not a replacement."""
    tid = add_ticket()
    make_attempts(renv, tid, 6)
    bump_db_attempt(tid, 1)

    assert set(runaway.stuck_ticket_ids()) == {tid}


def test_null_db_attempt_column_degrades_to_file_count_only(renv):
    """wq_show()'s `attempt` can come back None/missing on a ticket schema this module
    has never seen bump it (e.g. a hand-inserted fixture row) -- must not crash, must
    not be treated as a huge int, just falls back to the file count."""
    tid = add_ticket()
    make_attempts(renv, tid, 3)
    # deliberately do NOT call bump_db_attempt -- leave the column at its natural default

    assert runaway.stuck_ticket_ids(threshold=3)[tid]["attempts"] == 3


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
    """hold_released() runs the lint; never let a unit test read the live fleet.

    wq#1482: also stub `load_sweep` to a fresh, hitless sweep over the real
    allow.read_dirs roots, so this file's hold/release corpus (which predates the
    sweep gate) is not turned into permanent unresolved holds by a runtime sweep
    file this test suite never writes."""
    import datetime
    monkeypatch.setattr(capability_lint, "load_roster",
                        lambda refresh=False: capability_lint.Roster(names=["lumen"]))
    policy = capability_lint.load_policy()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    roots = {r: {"files": 0, "hits": []}
             for r in (policy.get("allow") or {}).get("read_dirs") or []}
    monkeypatch.setattr(capability_lint, "load_sweep",
                        lambda path=None: {"ts": ts, "roots": roots})


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
    write_failure(renv, tid, unreachable_paths=["/home/lumen/research_prompts/notes.md"])
    assert tid in runaway.harness_defect_ticket_ids()

    set_needs(tid, "dir(/home/lumen/research_prompts)")
    assert tid not in runaway.harness_defect_ticket_ids()


def test_hold_releases_on_a_tag_the_lint_derives_this_pass(renv):
    """Even before the write lands, the lint's own derivation covers the path — the
    hold and the pre-skip must agree about what the next spawn would reach."""
    tid = add_ticket("ops: read /home/lumen/research_prompts for the config")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/research_prompts"])

    assert tid not in runaway.harness_defect_ticket_ids()


def test_partial_coverage_keeps_the_hold(renv):
    tid = add_ticket("two paths, one granted")
    write_failure(renv, tid,
                  unreachable_paths=["/home/lumen/research_prompts", "/home/lumen/nowhere-at-all"])
    set_needs(tid, "dir(/home/lumen/research_prompts)")

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_v1_record_with_no_structured_paths_keeps_its_hold(renv):
    """Pre-slice-A failure.json always wrote unreachable_paths: [] (§2.5). An empty
    list is "we don't know", and a hold must never release on not knowing."""
    tid = add_ticket("old record, no structured fields")
    set_needs(tid, "dir(/home/lumen/research_prompts)")
    write_failure(renv, tid)  # no unreachable_paths key at all

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_non_path_defect_keeps_its_hold(renv):
    """WORKTREE_REPO_MISMATCH and hook crashes are what the backstop is FOR — only a
    defect this module can prove is fixed gets to bypass it."""
    tid = add_ticket("worktree/repo mismatch")
    set_needs(tid, "dir(/home/lumen/research_prompts)")
    write_failure(renv, tid, summary="WORKTREE_REPO_MISMATCH")

    assert tid in runaway.harness_defect_ticket_ids()


def test_a_lint_crash_never_releases_a_hold(renv, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("lint boom")
    monkeypatch.setattr(capability_lint, "derive", boom)
    tid = add_ticket("reads /home/lumen/research_prompts")
    set_needs(tid, "dir(/home/lumen/research_prompts)")
    write_failure(renv, tid, unreachable_paths=["/home/lumen/research_prompts"])

    assert tid in runaway.harness_defect_ticket_ids()
