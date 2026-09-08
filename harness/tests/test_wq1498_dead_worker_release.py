"""wq#1498 — `wq retire` on a per-ticket fworker identity poisons every future spawn of that
ticket.

`fworker.wq_identity(ticket_id)` (harness/fworker.py:490) mints the STABLE name
`fworker-wq<id>` — every attempt at a ticket claims under the identical name, on purpose
(wq#1116). It is never a dead session, only ever a currently-idle one. Before this patch,
`owner_ok`'s own refusal text recommended `wq retire fworker-wq<id>` for a stale claim under
that name; retiring it plants a permanent `retired_sessions` row, so the NEXT fworker
dispatched for the same ticket claims under the identical name and hits the STOP banner. Two
orchestrator sessions hit exactly that on 2026-09-08 (13 tickets, rows deleted by hand).

Two halves, both against the real `wq` binary and a scratch DB (same subprocess pattern as
test_wq1479_close_verdict.py — no in-process reimplementation):
  A. `wq retire fworker-wq<id>` refuses without `--dead-worker`; `wq release <id>
     --dead-worker` is the replacement stale-claim path, gated on an independent liveness
     check (no live session, and — for the fworker-wq<id> shape — no running
     `lumen-job-fworker-wq<id>-*` unit per a stubbed `lumen-run list`).
  B. the keeper: ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py --check
     proves the live wq still carries the patch.
"""
import json
import os
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
ENSURE = os.path.join(REPO, "ops", "wq1498-dead-worker-release",
                      "ensure_wq_dead_worker_release.py")

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


def add(env, title, lane="wq1498-lane"):
    p = wq_ok(env, "add", "--lane", lane, "--title", title, agent="wq1498-test-setup")
    return int(p.stdout.strip().split()[2])


def db(env):
    return sqlite3.connect(env["WQ_DB"], timeout=30)


def row(env, tid, *cols):
    c = db(env)
    try:
        return c.execute(f"SELECT {','.join(cols)} FROM tasks WHERE id=?", (tid,)).fetchone()
    finally:
        c.close()


def retired_sids(env):
    c = db(env)
    try:
        return {r[0] for r in c.execute("SELECT session_id FROM retired_sessions")}
    finally:
        c.close()


def activity_cmds(env, sid):
    c = db(env)
    try:
        return [r[0] for r in c.execute(
            "SELECT cmd FROM agent_activity WHERE session_id=? ORDER BY id", (sid,))]
    finally:
        c.close()


@pytest.fixture
def q(tmp_path):
    env = make_env(tmp_path)
    wq_ok(env, "list", agent="wq1498-test-setup")  # init() creates the DB
    return env


@pytest.fixture
def orch(q, tmp_path):
    """A registered orchestrator-class session: owner_ok's 'registered, has no excuse' branch,
    the caller shape that actually needs --dead-worker to release someone else's claim."""
    p = wq_ok(q, "whoami", "--register", "orch-wq1498-test", agent=None)
    sid = next(l.split(": ", 1)[1] for l in p.stdout.splitlines()
               if l.startswith("REGISTERED session: "))
    return sid


# ------------------------------------------------------------------- A. retire refuses


def test_retire_fworker_identity_is_refused(q):
    tid = add(q, "claimed by a per-ticket identity")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    p = wq(q, "retire", holder)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "wq#1498" in p.stdout

    assert holder not in retired_sids(q)
    assert row(q, tid, "status", "claimed_by") == ("claimed", holder)


def test_retire_fworker_identity_works_with_the_escape_hatch(q):
    tid = add(q, "genuinely retired for good")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    p = wq_ok(q, "retire", holder, "--dead-worker")
    assert f"RETIRED {holder}" in p.stdout
    assert holder in retired_sids(q)
    assert row(q, tid, "status")[0] == "open"


def test_retire_of_an_ordinary_session_is_unaffected(q):
    reg = wq_ok(q, "whoami", "--register", "some-ordinary-session", agent=None)
    sid = next(l.split(": ", 1)[1] for l in reg.stdout.splitlines()
               if l.startswith("REGISTERED session: "))
    p = wq_ok(q, "retire", sid)
    assert f"RETIRED {sid}" in p.stdout
    assert sid in retired_sids(q)


# ------------------------------------------------------------------- A. dead-worker release


def test_dead_worker_release_refused_without_the_flag(q, orch):
    tid = add(q, "stale claim, no flag yet")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    p = wq(q, "release", tid, agent=orch)
    assert p.returncode != 0
    assert row(q, tid, "status", "claimed_by") == ("claimed", holder)


def test_dead_worker_release_refused_when_unit_still_running(q, orch):
    tid = add(q, "not actually dead")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = holder
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode != 0, p.stdout + p.stderr
    assert row(env, tid, "status", "claimed_by") == ("claimed", holder)


def test_dead_worker_release_succeeds_and_logs_the_holder(q, orch):
    tid = add(q, "genuinely abandoned by a dead fworker")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = ""
    p = wq_ok(env, "release", tid, "--dead-worker", agent=orch)
    assert f"released {tid}" in p.stdout

    assert row(env, tid, "status", "claimed_by") == ("open", None)
    assert any(c.startswith(f"dead-worker-release:{holder}") for c in activity_cmds(env, orch))


# ------------------------------------------------------------------- B. the keeper


def test_the_live_wq_is_still_patched():
    """ops/wq841's lesson: `wq` is rewritten in place by whichever lane touches it next. This
    is the alarm for the wq#1498 patch specifically."""
    p = subprocess.run([sys.executable, ENSURE, "--check", "--json"],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#1498 dead-worker-release patch. "
        f"Repair: python3 {ENSURE} --apply\n{report['patches']}")
