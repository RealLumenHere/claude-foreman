"""wq#1539 — `wq release --dead-worker` printed the REFUSED owner banner before releasing.

`_finish` (harness for `wq release`/`wq done`/etc.) called `owner_ok()` first; `owner_ok()`
eprints its own `REFUSED: wq#<id> is held by ...` banner unconditionally on refusal, BEFORE
`_finish` got a chance to evaluate the wq#1498 `--dead-worker` override. So a fully successful
`wq release <id> --dead-worker` — holder verified dead, claim released, logged on the record —
opened its stderr with that REFUSED block anyway, ahead of the actual WARNING + released
lines. A caller reading only the first line, or a harness grepping stderr for REFUSED,
concluded the call failed when it hadn't. First hit live in wq#1536 (first live use of
wq#1498's release path).

The fix reorders `_finish`: the override's own eligibility (holder set, holder != caller,
`holder_is_dead()`) is checked BEFORE `owner_ok` is called at all, not after — so a
verified-dead holder never reaches `owner_ok`, and its banner never prints. `owner_ok` itself
is untouched; a still-alive holder (override not eligible) still reaches it and gets refused
exactly as before.

Same subprocess pattern as test_wq1498_dead_worker_release.py — no in-process
reimplementation, real `wq` binary against a scratch DB and a stubbed `lumen-run list`.
  A. the success path's stderr has no REFUSED anywhere.
  B. a still-alive holder still gets refused (owner_ok is reached, override not eligible) —
     unaffected by this fix.
  C. an ordinary (non-`--dead-worker`) owner conflict is unaffected — the generic banner still
     prints exactly as before.
  D. the keeper: ops/wq1539-release-dead-worker-order/ensure_wq_release_dead_worker_order.py
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
ENSURE = os.path.join(REPO, "ops", "wq1539-release-dead-worker-order",
                      "ensure_wq_release_dead_worker_order.py")

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


def add(env, title, lane="wq1539-lane"):
    p = wq_ok(env, "add", "--lane", lane, "--title", title, agent="wq1539-test-setup")
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
    wq_ok(env, "list", agent="wq1539-test-setup")  # init() creates the DB
    return env


@pytest.fixture
def orch(q):
    """A registered orchestrator-class session: owner_ok's 'registered, has no excuse' branch,
    the caller shape that actually needs --dead-worker to release someone else's claim."""
    p = wq_ok(q, "whoami", "--register", "orch-wq1539-test", agent=None)
    sid = next(l.split(": ", 1)[1] for l in p.stdout.splitlines()
               if l.startswith("REGISTERED session: "))
    return sid


# ------------------------------------------------------------------- A. success path is clean


def test_dead_worker_release_success_has_no_refused_on_stderr(q, orch):
    tid = add(q, "genuinely abandoned by a dead fworker")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = ""
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "REFUSED" not in p.stderr, f"success path must not print REFUSED: {p.stderr!r}"
    assert row(env, tid, "status", "claimed_by") == ("open", None)


# ------------------------------------------------------------------- B. still-alive holder


def test_dead_worker_release_still_alive_is_refused(q, orch):
    tid = add(q, "not actually dead")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    env = dict(q); env["FAKE_RUNNING_UNITS"] = holder
    p = wq(env, "release", tid, "--dead-worker", agent=orch)
    assert p.returncode != 0
    assert "liveness check found it still alive" in p.stderr
    assert row(env, tid, "status", "claimed_by") == ("claimed", holder)


# ------------------------------------------------------------------- C. ordinary conflict unaffected


def test_ordinary_release_conflict_still_prints_the_generic_banner(q, orch):
    tid = add(q, "claimed by someone else, no dead-worker flag")
    holder = f"fworker-wq{tid}"
    wq_ok(q, "claim", holder, "--id", tid)

    p = wq(q, "release", tid, agent=orch)
    assert p.returncode != 0
    assert f"REFUSED: wq#{tid} is held by '{holder}'" in p.stderr
    assert row(q, tid, "status", "claimed_by") == ("claimed", holder)


# ------------------------------------------------------------------- D. the keeper


def test_the_live_wq_is_still_patched():
    """ops/wq841's lesson: `wq` is rewritten in place by whichever lane touches it next. This
    is the alarm for the wq#1539 patch specifically."""
    p = subprocess.run([sys.executable, ENSURE, "--check", "--json"],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#1539 dead-worker release-order patch. "
        f"Repair: python3 {ENSURE} --apply\n{report['patches']}")
