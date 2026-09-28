"""wq#2997 — `wq done` closed a 'user'-lane ticket that `wq claim` refused.

`cmd_claim` refuses the 'user' lane unconditionally (wq:1559 "'user' lane is Frank-only —
never agent-claimable"; the --id form at wq:1593; `wq hold` too, at wq:3078). `cmd_done` had
no matching check: `owner_ok()` returns True whenever `claimed_by` is falsy, and a
'user'-lane ticket can never carry a live claim in the first place (claim always refuses it),
so that ownership gate was never even in a position to catch this. On 2026-09-28,
`wq claim --id 1304` correctly refused (user lane), but `wq done 1304 --no-artifact ...`
still closed it clean with no claim ever made. Frank had directed that specific close in
chat, so the outcome was wanted — but the two verbs disagreed on the lane rule.

Fix (ops/wq2997-user-lane-done-gate/ensure_wq_user_lane_done_gate.py): `done` now refuses a
'user'-lane ticket the same way `claim`/`hold` do, unless the caller passes
`--frank-directed "<reason>"` — required instead of a plain match to claim's blanket refusal
because a 'user'-lane ticket can never be claimed first, so there is no claim record to point
at for "who authorized this". The reason is stamped onto the ticket as
`[FRANK-DIRECTED: ...]`, on the record, the same way `--no-artifact`/`--unlanded`/`--reclose`
stamp their own overrides — done's other evidence gates still apply on top.

Two halves, both against the real `wq` binary and a scratch DB (same subprocess pattern as
test_wq1498_dead_worker_release.py — no in-process reimplementation):
  A. `done` on a 'user'-lane ticket refuses without --frank-directed, succeeds (and stamps
     the reason) with it, and an ordinary lane's `done` is completely unaffected.
  B. the keeper: ops/wq2997-user-lane-done-gate/ensure_wq_user_lane_done_gate.py --check
     proves the live wq still carries the patch.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
ENSURE = os.path.join(REPO, "ops", "wq2997-user-lane-done-gate",
                      "ensure_wq_user_lane_done_gate.py")


def make_env(tmp_path):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env.pop("WQ_AGENT", None)
    env["WQ_DISPATCHER_CONFIG"] = str(tmp_path / "no-such-dispatcher-config.json")
    env["WQ_FOREMAN_ROUTER"] = "/nonexistent/wq2997/router.py"  # wq#2139 lane guard fails open
    env["WQ_SCRATCH_ROOT"] = str(tmp_path / "scratch")
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


def add(env, title, lane="wq2997-lane", *extra):
    p = wq_ok(env, "add", "--lane", lane, "--title", title, *extra, agent="wq2997-test-setup")
    m = re.search(r"added task (\d+)", p.stdout)
    assert m, p.stdout
    return int(m.group(1))


def row(env, tid, *cols):
    c = sqlite3.connect(env["WQ_DB"], timeout=30)
    try:
        return c.execute(f"SELECT {','.join(cols)} FROM tasks WHERE id=?", (tid,)).fetchone()
    finally:
        c.close()


@pytest.fixture
def q(tmp_path):
    env = make_env(tmp_path)
    wq_ok(env, "list", agent="wq2997-test-setup")  # init() creates the DB
    return env


# ------------------------------------------------------------------- A. claim vs. done agree


def test_claim_refuses_a_user_lane_ticket(q):
    """Baseline this patch must not disturb: claim already refuses 'user' unconditionally."""
    tid = add(q, "user lane, claim baseline", "user", "--no-probe", "wq2997 test")
    p = wq(q, "claim", "--id", tid)
    assert p.returncode != 0, p.stdout + p.stderr
    assert "user' lane" in p.stdout
    assert row(q, tid, "status", "claimed_by") == ("open", None)


def test_done_refuses_a_user_lane_ticket_without_the_flag(q):
    """This is the wq#2997 bug: before the patch, this call closed the ticket clean."""
    tid = add(q, "user lane, no flag", "user", "--no-probe", "wq2997 test")
    p = wq(q, "done", tid, "--no-artifact", "wq2997 test probe")
    assert p.returncode != 0, p.stdout + p.stderr
    assert "wq#2997" in p.stdout or "wq#2997" in p.stderr
    assert row(q, tid, "status")[0] == "open"


def test_done_succeeds_on_a_user_lane_ticket_with_frank_directed_and_stamps_the_reason(q):
    tid = add(q, "user lane, frank directed", "user", "--no-probe", "wq2997 test")
    p = wq_ok(q, "done", tid, "--no-artifact", "wq2997 test probe",
             "--frank-directed", "Frank directed this in #ops chat 2026-09-28")
    assert f"done {tid}" in p.stdout

    status, notes = row(q, tid, "status", "notes")
    assert status == "done"
    assert "[FRANK-DIRECTED: Frank directed this in #ops chat 2026-09-28]" in notes


def test_done_on_an_ordinary_lane_is_unaffected(q):
    tid = add(q, "ordinary lane, unaffected")
    wq_ok(q, "claim", "wq2997-holder", "--id", tid)
    p = wq_ok(q, "done", tid, "--no-artifact", "wq2997 test probe", agent="wq2997-holder")
    assert f"done {tid}" in p.stdout
    assert row(q, tid, "status")[0] == "done"


def test_frank_directed_alone_does_not_skip_the_evidence_gate(q):
    """--frank-directed lifts the lane block, not done's other evidence requirements."""
    tid = add(q, "user lane, no evidence at all", "user", "--no-probe", "wq2997 test")
    p = wq(q, "done", tid, "--frank-directed", "Frank directed this in chat")
    assert p.returncode != 0, p.stdout + p.stderr
    assert "wq#759" in p.stdout or "wq#759" in p.stderr
    assert row(q, tid, "status")[0] == "open"


# ------------------------------------------------------------------- B. the keeper


def test_the_live_wq_is_still_patched():
    """ops/wq841's lesson: `wq` is rewritten in place by whichever lane touches it next. This
    is the alarm for the wq#2997 patch specifically."""
    p = subprocess.run([sys.executable, ENSURE, "--check", "--json"],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#2997 user-lane done-gate patch. "
        f"Repair: python3 {ENSURE} --apply\n{report['patches']}")
