"""wq#1894 ACCEPTANCE: a fworker unit stopped (SIGTERMed) while its child is still
running, before attempt-N.json exists, must not leave the ticket 'claimed' until lease
expiry (wq#1852 sat 6h) nor its dispatch-spawn reservation held until
SPAWN_RESERVE_TTL_S. handle_external_sigterm() (harness/fworker.py) now charges a
minimal KILLED/signal attempt-N.json, releases the wq claim, and (as a side effect of
write_attempt_state -> _settle_spawn_reservation, wq#1893) settles the reservation.

Same subprocess-against-a-fake-claude-shim pattern as test_fworker_integration.py, but
the fake child ("hang" scenario: emits init/start, then sleeps) is left running and
SIGTERM is sent to the fworker PROCESS itself (not the internal watchdog's own
child-directed kill, which is exercised elsewhere by test_kill_group.py and
test_fworker_integration.py's watchdog tests).
"""
import asyncio
import json
import os
import signal
import subprocess
import sys
import time

HARNESS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS_DIR)
import spawner  # noqa: E402 — wq#1893: assert the reservation gets settled too

FWORKER = os.path.join(HARNESS_DIR, "fworker.py")
FAKE_CLAUDE = os.path.join(TESTS_DIR, "fake_claude.py")
FAKE_GOVERNOR = os.path.join(TESTS_DIR, "fake_governor.py")
WQ_BIN = "/home/lumen/bin/wq"


def make_scratch_repo(tmp_path):
    repo = tmp_path / "scratch_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@lumen"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "README.md").write_text("scratch\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return str(repo)


def make_scratch_env(tmp_path, ticket, scenario):
    wq_db = str(tmp_path / "scratch_wq.db")
    events_dir = str(tmp_path / "events")
    runs_root = str(tmp_path / "_runs")
    worktrees_root = str(tmp_path / "worktrees")
    reserve_db = str(tmp_path / "spawn_reserve.db")
    os.makedirs(events_dir, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "WQ_DB": wq_db,
        "WQ_AGENT": "test-fworker-agent",
        "FOREMAN_EVENTS_DIR": events_dir,
        "FOREMAN_RUNS_ROOT": runs_root,
        "FOREMAN_WORKTREES_ROOT": worktrees_root,
        "FOREMAN_STOP_FILE": str(tmp_path / "STOP_nonexistent"),
        "FOREMAN_STATE_FILE": str(tmp_path / "state.json"),
        "FOREMAN_GOVERNOR_CLI": FAKE_GOVERNOR,
        "FOREMAN_WQ_BIN": WQ_BIN,
        "FOREMAN_LUMEN_PR_BIN": "/bin/false",
        "FOREMAN_NTFY_BIN": "/bin/true",
        "FOREMAN_SPAWN_RESERVE_DB": reserve_db,
        "FAKE_CLAUDE_SCENARIO": scenario,
        "FAKE_CLAUDE_TICKET": str(ticket),
        "FAKE_CLAUDE_GUARD_SH": os.path.join(HARNESS_DIR, "guard.sh"),
        "FAKE_CLAUDE_WQ_BIN": WQ_BIN,
    })
    return env, runs_root, worktrees_root, reserve_db


def wq_add(env, title):
    p = subprocess.run([WQ_BIN, "add", "--title", title], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def wq_status(env, ticket_id):
    p = subprocess.run([WQ_BIN, "show", str(ticket_id)], capture_output=True, text=True, env=env)
    for line in p.stdout.splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "status":
            return v.strip()
    return None


def test_sigterm_to_fworker_charges_killed_attempt_and_releases_claim_and_reservation(tmp_path):
    repo = make_scratch_repo(tmp_path)
    env, runs_root, worktrees_root, reserve_db = make_scratch_env(tmp_path, 0, "hang")
    ticket_id = wq_add(env, "scratch sigterm-cancel ticket")
    env["FAKE_CLAUDE_TICKET"] = str(ticket_id)
    # wq#3222: the fake child writes this at startup, i.e. after do_attempt's
    # set_sigterm_context() and run_child's Popen. The worktree appears ~110 lines of
    # setup earlier, so "worktree exists + 0.5s" lost the race on a loaded box: SIGTERM
    # landed before the context was armed and no attempt-1.json was charged.
    child_started = str(tmp_path / "fake-claude-started.json")
    env["FAKE_CLAUDE_ENV_DUMP"] = child_started

    # pre-seed the reservation the real dispatcher (spawner.dispatch_one) would have
    # taken before Popen'ing this fworker — otherwise the "settled" assertion below is
    # vacuously true (nothing was ever held).
    spawner.SPAWN_RESERVE_DB = reserve_db
    key = f"dispatch-spawn:{ticket_id}:1"
    assert spawner.reserve_spawn(ticket_id, 1, "fake-dispatcher-owner")

    proc = subprocess.Popen(
        [sys.executable, FWORKER, "run", "--ticket", str(ticket_id), "--repo", repo,
         "--model", "claude-sonnet-5", "--effort", "high", "--archetype", "mechanical-edit",
         "--claude-bin", FAKE_CLAUDE, "--worker-id", f"test-fworker-wq{ticket_id}",
         # generous — must outlast this test, so ONLY our external SIGTERM ends the run
         "--wall-clock-timeout", "3600", "--frame-gap-timeout", "3600",
         "--post-result-grace", "3600"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        worktree = os.path.join(worktrees_root, f"wq{ticket_id}-a1")
        deadline = time.time() + 60
        while time.time() < deadline:
            if (os.path.exists(child_started) and wq_status(env, ticket_id) == "claimed"
                    and os.path.isdir(worktree)):
                break
            time.sleep(0.1)
        else:
            proc.kill()
            raise AssertionError(f"fworker never claimed+spawned:\n{proc.stdout.read()}")

        assert not os.path.exists(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")), \
            "test is only meaningful before attempt-1.json exists"

        os.kill(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    assert proc.returncode == 143, f"expected SIGTERM exit (143), got {proc.returncode}:\n{out}"

    with open(os.path.join(runs_root, f"wq{ticket_id}", "attempt-1.json")) as f:
        record = json.load(f)
    assert record["classification"] == "KILLED", record
    assert record["killed_reason"] == "signal", record

    assert wq_status(env, ticket_id) == "open", "claim must be released, not held, on SIGTERM"

    store = spawner.ControlStore(reserve_db)
    assert asyncio.run(store.idem_get(key)) is None, \
        "SIGTERM path must settle (clear) the spawn reservation, not leave it held"
