"""wq#2491 chaos test: SIGKILL a worker mid-claim, assert its ticket is reclaimed.

harness/fworker.py's SIGTERM handler (search "SIGTERM: wq#" in fworker.py) explicitly
releases a worker's wq claim on the way out. SIGKILL is uncatchable — nothing runs on
that path, so a SIGKILLed worker's ticket depends entirely on wq's own lease-expiry
reclaim (bin/wq's provisional lease + `wq reap`, RELIABILITY_SPEC.md §E / wq#647) to
come back. This test proves that fallback actually works, and — the property most
likely to regress, and the one that would hurt — that killing the process is NOT by
itself what triggers reclaim: only an actually-expired lease is, no matter how quiet
(or dead) the holder is otherwise.

Companion to test_wq_reap.py (DB-only reclaim, no real process) and test_reaper.py
(the independent sweep script); this one adds the missing piece those don't cover — a
real OS process standing in for the worker, killed with a real SIGKILL, same
zero-quota subprocess pattern as test_fworker_integration.py.

Blast radius (wq#2491 AC4): WQ_DB points at a tmp_path scratch file, never the live
queue; the only PID ever signalled is one this test spawned itself (`sleep 300`),
never a PID found by scanning the process table.

Correction to the wq#2491 ticket text: it names WQ_STALE_MIN as the knob to shorten
for a fast test. Reading bin/wq's own docstring (wq#530, lines 27-34) shows
WQ_STALE_MIN frees a BASE NAME (session identity) only; the mechanism that frees a
TICKET is the provisional lease on the row (`lease_expires`, governed by
WQ_PROVISIONAL_MIN / --ttl), which is what both cmd_claim's housekeeping
(bin/wq:1556-1558) and `wq reap` (bin/wq:3229-3231) key off. This test follows the
existing convention already established in test_wq_reap.py / test_reaper.py and backs
`lease_expires` into the past directly rather than waiting out a real TTL.
"""
import datetime
import os
import signal
import sqlite3
import subprocess

WQ_BIN = "/home/lumen/bin/wq"
FMT = "%Y-%m-%d %H:%M:%S"


def make_env(tmp_path, agent):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env["WQ_AGENT"] = agent
    return env


def wq(env, *args):
    return subprocess.run([WQ_BIN] + list(args), capture_output=True, text=True, env=env)


def wq_add(env, title):
    p = wq(env, "add", "--title", title)
    assert p.returncode == 0, p.stdout + p.stderr
    return int(p.stdout.strip().split()[2])


def wq_show(env, ticket_id):
    p = wq(env, "show", str(ticket_id))
    out = {}
    for line in p.stdout.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def force_expire(db_path, ticket_id, minutes_ago=5):
    past = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=minutes_ago)).strftime(FMT)
    c = sqlite3.connect(db_path)
    c.execute("UPDATE tasks SET lease_expires=? WHERE id=?", (past, ticket_id))
    c.commit()
    c.close()


def spawn_fake_worker():
    """A real OS process this test owns end-to-end, standing in for a fworker build
    worker. Never signal any PID the test did not itself spawn (wq#2491 AC4)."""
    return subprocess.Popen(["sleep", "300"])


def sigkill_and_reap_own_worker(worker):
    os.kill(worker.pid, signal.SIGKILL)
    worker.wait(timeout=5)


def test_sigkilled_worker_ticket_is_reclaimed_and_logged_loudly(tmp_path):
    worker = spawn_fake_worker()
    holder = f"lumen:{worker.pid}"
    env = make_env(tmp_path, agent=holder)
    try:
        ticket_id = wq_add(env, "scratch sigkill-chaos ticket")
        claim = wq(env, "claim", "--id", str(ticket_id))
        assert claim.returncode == 0, claim.stdout + claim.stderr
        assert wq_show(env, ticket_id)["status"] == "claimed"

        # the chaos event: an uncatchable hard kill, no cleanup handler runs
        sigkill_and_reap_own_worker(worker)

        # simulate the provisional lease having actually expired — real TTL is
        # minutes, too slow to wait out here (see module docstring correction)
        force_expire(env["WQ_DB"], ticket_id)

        result = wq(env, "reap")
        assert result.returncode == 0, result.stdout + result.stderr
        reclaim_line = next(
            (l for l in result.stdout.splitlines() if l.startswith(f"REAPED #{ticket_id}")),
            None)
        assert reclaim_line is not None, result.stdout
        assert f"holder={holder}" in reclaim_line
        assert "reaped 1 ticket(s)" in result.stdout

        after = wq_show(env, ticket_id)
        assert after["status"] == "open"
        assert after["claimed_by"] in (None, "", "None")
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)


def test_live_lease_is_never_reclaimed_no_matter_how_quiet(tmp_path):
    """The important negative (wq#2491 AC3): SIGKILLing the process is NOT what
    triggers reclaim. Only an expired lease is. A worker that dies with time still
    left on its lease — and sends no heartbeat, ever, from that point on — must be
    left alone. A liveness-based reclaim would pass the positive test above too, so
    this is the assertion that actually distinguishes "reclaims dead workers" from
    "reclaims workers wq merely hasn't heard from in a while", which bin/wq's own
    docstring (line 29, and line 33's "time alone never releases a claim") says must
    never happen.
    """
    worker = spawn_fake_worker()
    holder = f"lumen:{worker.pid}"
    env = make_env(tmp_path, agent=holder)
    try:
        ticket_id = wq_add(env, "scratch live-lease ticket")
        claim = wq(env, "claim", "--id", str(ticket_id), "--ttl", "240")
        assert claim.returncode == 0, claim.stdout + claim.stderr

        # same chaos event as the positive case — the process is just as dead
        sigkill_and_reap_own_worker(worker)
        # ...but no force_expire: the lease is still fully live

        result = wq(env, "reap")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "reaped 0 ticket(s)" in result.stdout
        assert f"REAPED #{ticket_id}" not in result.stdout

        after = wq_show(env, ticket_id)
        assert after["status"] == "claimed"
        assert after["claimed_by"] == holder
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(timeout=5)
