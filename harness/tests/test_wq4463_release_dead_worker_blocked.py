"""wq#4463 — `wq release --dead-worker` on a BLOCKED ticket flipped it to open and wiped
block_reason.

Observed 2026-10-21 on ticket 2842 (blocked, block_reason 1359 chars): after `wq release 2842
--dead-worker` it read status open, block_reason NULL. The wq#1498/wq#1539 dead-worker
override only cleared the dead holder's stale claim and then fell through into the generic
`new_status == 'open'` UPDATE in `_finish`, which unconditionally sets status='open' and
block_reason=NULL/blocked_at=NULL. At `open` the ~20min foreman dispatcher can redispatch a
ticket someone deliberately blocked (often Frank's own call) before anyone notices. 26 blocked
tickets carried a dead fworker claimed_by at the time this was found, so the footgun was live.

The fix: inside the dead-worker-override branch, when the ticket's CURRENT status is
'blocked', `_finish` clears claimed_by/lease/hold and releases subsystem locks directly, then
returns before the generic UPDATE runs. status and block_reason/blocked_at are left
untouched. A non-blocked holder (open/claimed) is unaffected.

Same subprocess pattern as test_wq1539_release_dead_worker_order.py — no in-process
reimplementation, real `wq` binary against a scratch DB and a stubbed `lumen-run list`.
  A. a blocked ticket's dead-worker release stays blocked, keeps block_reason, clears
     claimed_by.
  B. an ordinary (non-blocked) dead-worker release is unaffected — still flips to open.
  C. a still-alive holder is still refused (wq#1539 unaffected).
  D. the keeper: ops/wq4463-release-dead-worker-blocked/ensure_wq4463_release_dead_worker_blocked.py
     --check proves the live wq still carries the patch.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
ENSURE = os.path.join(REPO, "ops", "wq4463-release-dead-worker-blocked",
                      "ensure_wq4463_release_dead_worker_blocked.py")

FAKE_LUMEN_RUN = """#!/usr/bin/env python3
import os, sys
if len(sys.argv) > 1 and sys.argv[1] == "list":
    for name in [n for n in os.environ.get("FAKE_RUNNING_UNITS", "").split(",") if n]:
        print(f"STUB   lumen-job-{name}-1786850106.service   running")
sys.exit(0)
"""


def make_env(tmp_path):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env.pop("WQ_AGENT", None)
    env["WQ_DISPATCHER_CONFIG"] = str(tmp_path / "no-such-dispatcher-config.json")
    env["WQ_SCRATCH_ROOT"] = str(tmp_path / "scratch")
    fake_bin = tmp_path / "fake-lumen-run"
    fake_bin.write_text(FAKE_LUMEN_RUN)
    fake_bin.chmod(0o755)
    env["WQ_LUMEN_RUN_BIN"] = str(fake_bin)
    env.setdefault("FAKE_RUNNING_UNITS", "")
    return env


def wq(env, *args, agent=None):
    e = dict(env)
    if agent is not None:
        e["WQ_AGENT"] = agent
    return subprocess.run([WQ_BIN] + [str(x) for x in args],
                          capture_output=True, text=True, env=e)


def wq_ok(env, *args, agent=None):
    p = wq(env, *args, agent=agent)
    assert p.returncode == 0, f"{args} -> {p.returncode}\n{p.stdout}\n{p.stderr}"
    return p


def add(env, title, lane="wq4463-lane"):
    p = wq_ok(env, "add", "--lane", lane, "--title", title, agent="wq4463-test-setup")
    return int(p.stdout.strip().split()[2])


def row(env, tid, *cols):
    c = sqlite3.connect(env["WQ_DB"], timeout=30)
    try:
        return c.execute(f"SELECT {','.join(cols)} FROM tasks WHERE id=?", (tid,)).fetchone()
    finally:
        c.close()


@pytest.fixture
def q(tmp_path):
    env = make_env(tmp_path)
    wq_ok(env, "list", agent="wq4463-test-setup")  # init() creates the DB
    return env


@pytest.fixture
def orch(q):
    """A registered orchestrator-class session — the caller shape that needs --dead-worker
    to release someone else's claim."""
    p = wq_ok(q, "whoami", "--register", "orch-wq4463-test", agent=None)
    sid = next(l.split(": ", 1)[1] for l in p.stdout.splitlines()
               if l.startswith("REGISTERED session: "))
    return sid


# ------------------------------------------------------------ A. blocked ticket stays blocked


def test_dead_worker_release_on_blocked_ticket_stays_blocked(q, orch):
    tid = add(q, "deliberately blocked, holder went dead")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)
    wq_ok(q, "block", tid, "Frank: parked pending a physical check", agent=holder)
    assert row(q, tid, "status", "block_reason") == (
        "blocked", "Frank: parked pending a physical check")

    env = dict(q); env["FAKE_RUNNING_UNITS"] = ""
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "stayed blocked" in p.stderr

    status, claimed_by, block_reason = row(env, tid, "status", "claimed_by", "block_reason")
    assert status == "blocked", "status must not flip to open (wq#4463)"
    assert claimed_by is None, "the dead holder's stale claim must still clear"
    assert block_reason == "Frank: parked pending a physical check", \
        "block_reason must survive the release (wq#4463)"


# ------------------------------------------------------------ B. ordinary release unaffected


def test_dead_worker_release_on_open_claim_still_opens(q, orch):
    tid = add(q, "genuinely abandoned by a dead fworker, never blocked")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = ""
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode == 0, p.stdout + p.stderr
    assert row(env, tid, "status", "claimed_by") == ("open", None)


# ------------------------------------------------------------ C. still-alive holder unaffected


def test_dead_worker_release_still_alive_is_refused(q, orch):
    tid = add(q, "blocked, but holder is still alive")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)
    wq_ok(q, "block", tid, "parked", agent=holder)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = holder
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode != 0
    assert "liveness check found it still alive" in p.stderr
    assert row(env, tid, "status", "claimed_by") == ("blocked", holder)


# ------------------------------------------------------------ D. the keeper


def test_the_live_wq_is_still_patched():
    """wq is rewritten in place by whichever lane touches it next. This is the alarm for the
    wq#4463 patch specifically."""
    p = subprocess.run([sys.executable, ENSURE, "--check", "--json"],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#4463 blocked-stays-blocked patch. "
        f"Repair: python3 {ENSURE} --apply\n{report['patches']}")
