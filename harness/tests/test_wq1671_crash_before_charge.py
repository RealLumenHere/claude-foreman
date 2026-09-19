"""wq#1671: crash-before-charge blindness.

An unhandled exception in do_attempt() (or run_fire_ticket()) -- before its own
write_attempt_state() ever runs -- used to leave the ticket claimed-then-released with
NOTHING on disk: no attempt-N.json (self_heal.attempt_count/runaway.stuck_ticket_ids
never see it) and no failure.json (runaway.harness_defect_ticket_ids, wq#1415, never
sees it either). handle_unhandled_crash() closes that gap the same way wq#1331's
handle_spawn_refused() already does for a SpawnRefused: write a failure.json triaged
HARNESS_DEFECT, release the ticket to open, and deliberately do NOT write an
attempt-N.json (this must not eat into RUNAWAY_MAX_ATTEMPTS/circuit_breaker budget for
a crash that has nothing to do with the ticket's own difficulty).

Integration style matches test_spawn_refused.py's corrupted-origin test: real `wq`
binary against a scratch DB, real fworker.py subprocess, no mocks on the crash path
itself -- the crash is a genuine `git worktree add` failure (wq#649's own repro shape),
not a simulated one.
"""
import json
import os
import subprocess
import sys

import pytest

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HARNESS_DIR)
import fworker  # noqa: E402
import failure_record  # noqa: E402

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
WQ_BIN = "/home/lumen/bin/wq"
FAKE_CLAUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_claude.py")
FAKE_GOVERNOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_governor.py")


def base_env(tmp_path):
    runs_root = str(tmp_path / "_runs")
    worktrees_root = str(tmp_path / "worktrees")
    env = dict(os.environ)
    env.update({
        "WQ_DB": str(tmp_path / "scratch_wq.db"),
        "WQ_AGENT": "test-crash-before-charge-agent",
        "FOREMAN_EVENTS_DIR": str(tmp_path / "events"),
        "FOREMAN_RUNS_ROOT": runs_root,
        "FOREMAN_WORKTREES_ROOT": worktrees_root,
        "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent"),
        "FOREMAN_STATE_FILE": str(tmp_path / "state.json"),
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_NTFY_BIN": "/bin/true",
        "FOREMAN_NO_REFILL": "1",
    })
    os.makedirs(env["FOREMAN_EVENTS_DIR"], exist_ok=True)
    return env, runs_root, worktrees_root


def add_ticket(env, title="scratch crash-before-charge ticket"):
    p = subprocess.run([WQ_BIN, "add", "--title", title],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


# ---- integration: a real git-plumbing crash reaches the new handler ------------------

def test_worktree_add_crash_writes_uncharged_harness_defect_record(tmp_path):
    # `repo` is deliberately NOT a git repository at all -- a regular FILE, which git
    # can never resolve from any TMPDIR (a bare directory under tmp_path gets walked up
    # to the worktree's own .git and ADOPTED, leaking chore/wq1-scratch-* branches;
    # wq#2012, wq#2034) -- make_worktree()'s own
    # `git worktree add` call fails for real, hits its `raise RuntimeError(f"git
    # worktree add failed: ...")` (fworker.py, wq#649's own repro shape), and nothing
    # upstream of cmd_run's outer except catches it.
    repo = str(tmp_path / "not_a_repo")
    with open(repo, "w") as f:
        f.write("not a git repository\n")

    env, runs_root, worktrees_root = base_env(tmp_path)
    ticket_id = add_ticket(env)

    r = subprocess.run(
        [sys.executable, FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
         "--model", "claude-sonnet-5", "--effort", "high",
         "--archetype", "mechanical-edit", "--claude-bin", FAKE_CLAUDE,
         "--worker-id", f"test-fworker-wq{ticket_id}"],
        capture_output=True, text=True, env=env, timeout=120)

    assert r.returncode == fworker.EXIT_CRASH, r.stdout + r.stderr
    assert "CRASH" in r.stdout

    # ticket released back to open -- not left claimed, not blocked.
    show = subprocess.run([WQ_BIN, "show", str(ticket_id), "--json"],
                          capture_output=True, text=True, env=env)
    row = json.loads(show.stdout)
    assert row["status"] == "open"

    run_dir = os.path.join(runs_root, f"wq{ticket_id}")
    # attempt NOT charged -- same contract as handle_spawn_refused: a harness-side
    # crash must not eat into the ticket's real attempt budget.
    assert not [f for f in os.listdir(run_dir) if f.startswith("attempt-")]

    # ...but failure.json now lands (this is the wq#1671 fix -- before it, run_dir
    # would be EMPTY here and runaway.harness_defect_ticket_ids() would never see this
    # ticket at all).
    with open(os.path.join(run_dir, "failure.json")) as f:
        rec = json.load(f)
    assert rec["classification"] == "LAUNCH_FAILURE"
    assert rec["triage"] == failure_record.TRIAGE_HARNESS_DEFECT
    assert "unhandled exception in attempt" in rec["summary"]
    assert ticket_id == rec["ticket"]


# ---- unit-level: the handler itself, independent of how the crash was triggered ------

@pytest.fixture
def crash_env(tmp_path, monkeypatch):
    runs_root = tmp_path / "_runs"
    runs_root.mkdir()
    env, _, _ = base_env(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(fworker, "RUNS_ROOT", str(runs_root))
    monkeypatch.setattr(fworker, "WQ_BIN", WQ_BIN)
    return {"runs_root": runs_root}


class _FakeArgs:
    def __init__(self, ticket, repo="/home/lumen/foreman"):
        self.ticket = ticket
        self.repo = repo


def test_handle_unhandled_crash_releases_and_records(crash_env):
    tid = add_ticket(dict(os.environ))
    # claim it first so there's something real to release -- mirrors the state
    # do_attempt() actually runs in (claim already succeeded by the time it crashes).
    # No positional agent: relies on WQ_AGENT (set by the crash_env fixture) the same
    # way wq_release() below will -- real fworker does this too (cmd_run pins
    # os.environ["WQ_AGENT"] = wq_identity(ticket) before claiming, precisely so the
    # claim and every later wq call agree on who's asking).
    p = subprocess.run([WQ_BIN, "claim", "--id", str(tid)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr

    rc = fworker.handle_unhandled_crash(
        _FakeArgs(tid), tid, 7, "Traceback (most recent call last):\nRuntimeError: boom",
        context="attempt 7")

    assert rc == fworker.EXIT_CRASH
    show = subprocess.run([WQ_BIN, "show", str(tid), "--json"],
                          capture_output=True, text=True)
    assert json.loads(show.stdout)["status"] == "open"

    d = crash_env["runs_root"] / f"wq{tid}"
    assert not [f for f in os.listdir(d) if f.startswith("attempt-")]
    rec = json.loads((d / "failure.json").read_text())
    assert rec["triage"] == failure_record.TRIAGE_HARNESS_DEFECT
    assert rec["classification"] == "LAUNCH_FAILURE"
    assert "boom" in rec["summary"]
    assert "attempt 7" in rec["summary"]


def test_handle_unhandled_crash_survives_a_failure_record_write_error(crash_env, monkeypatch):
    """The handler's own try/except around the record write (wq#1671 docstring: 'this
    handler must never itself block the release') means a broken failure_record call
    still releases the claim -- the crash record is best-effort, the release is not."""
    tid = add_ticket(dict(os.environ))
    subprocess.run([WQ_BIN, "claim", "--id", str(tid)],
                   capture_output=True, text=True, check=True)

    def boom(*a, **k):
        raise ValueError("record machinery itself is broken")
    monkeypatch.setattr(failure_record, "build_failure_record", boom)

    rc = fworker.handle_unhandled_crash(_FakeArgs(tid), tid, 1, "tb text", context="attempt 1")

    assert rc == fworker.EXIT_CRASH
    show = subprocess.run([WQ_BIN, "show", str(tid), "--json"],
                          capture_output=True, text=True)
    assert json.loads(show.stdout)["status"] == "open"  # released despite the record failure
