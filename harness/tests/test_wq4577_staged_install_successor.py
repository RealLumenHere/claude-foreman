"""wq#4577 — closure guard: `wq_close.py` refuses a close whose notes admit the deliverable
is staged for another session to deploy, unless a successor ticket deps-links the close or
an explicit override is on the record.

The incident this class-fixes (wq#4573's judgment on wq#4567-vs-wq#4540, reports/
wq4573_judgment_wq4540_boot_budget_disposition.md): the staging pattern (wq#4537/#4539/#4540)
is correct doctrine — a worker walled out of a live path builds+tests against fixtures and
ships ops/<ticket>/install.sh for a session that holds the grant. wq#4540 closed `done` this
way 4 minutes after wq#4537, and nothing filed the deploy: its own acceptance probe sat
unmeasured until a verify pass caught it 3h later (ISSUE-LOG 1537). The only defence was a
human noticing.

Same subprocess-against-a-scratch-DB pattern as test_wq1479_close_verdict.py: drives the
real `wq_close.py` (and the real `wq` binary underneath it) against a throwaway queue.db.
"""
import os
import subprocess
import sys

WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
WQ_CLOSE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "wq_close.py")

STAGED_NOTES = ("Built and tested against fixtures; shipped ops/9999/install.sh for a "
                "session with fs access. This session is staged, unreachable from the "
                "live target.")


def make_env(tmp_path, agent="wq4577-test-agent"):
    env = dict(os.environ)
    env["WQ_DB"] = str(tmp_path / "scratch_wq.db")
    env["WQ_AGENT"] = agent
    env["WQ_BIN"] = WQ_BIN
    return env


def wq(env, *args, agent=None):
    e = dict(env)
    if agent:
        e["WQ_AGENT"] = agent
    return subprocess.run([WQ_BIN] + [str(a) for a in args],
                          capture_output=True, text=True, env=e)


def wq_close(env, *args, agent=None):
    e = dict(env)
    if agent:
        e["WQ_AGENT"] = agent
    return subprocess.run([sys.executable, WQ_CLOSE] + [str(a) for a in args],
                          capture_output=True, text=True, env=e)


def new_ticket(env, title="wq4577 scratch ticket", lane="auto", deps=None):
    args = ["add", "--lane", lane, "--title", title]
    if deps:
        args += ["--deps", deps]
    p = wq(env, *args)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout.strip().split()[2]


def status_of(env, tid):
    for line in wq(env, "show", tid).stdout.splitlines():
        if line.startswith("status"):
            return line.split(":", 1)[1].strip()
    return None


def tail1(text):
    lines = [l for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else ""


def test_staged_notes_with_no_successor_refuses(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)

    p = wq_close(env, tid, "--notes", STAGED_NOTES, "--no-artifact", "staged for handoff")
    assert p.returncode == 1, p.stdout + p.stderr
    last = tail1(p.stdout)
    assert last.startswith(f"NOT-CLOSED wq#{tid} step=staged-install-successor"), last
    assert "wq#4577" in last
    assert status_of(env, tid) != "done"


def test_staged_notes_with_deps_linking_successor_closes(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)
    successor = new_ticket(env, title="wq4577 successor: deploy the staged bundle", deps=tid)

    p = wq_close(env, tid, "--notes", STAGED_NOTES, "--no-artifact", "staged for handoff")
    assert p.returncode == 0, p.stdout + p.stderr
    assert tail1(p.stdout) == f"CLOSED wq#{tid}"
    assert status_of(env, tid) == "done"

    show = wq(env, "show", tid)
    assert f"[STAGED-SUCCESSOR: wq#{successor}]" in show.stdout


def test_staged_notes_with_explicit_override_closes(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)

    p = wq_close(env, tid, "--notes", STAGED_NOTES, "--no-artifact", "staged for handoff",
                 "--staged-install-successor", "no successor needed, Frank deployed it by hand")
    assert p.returncode == 0, p.stdout + p.stderr
    assert tail1(p.stdout) == f"CLOSED wq#{tid}"

    show = wq(env, "show", tid)
    assert "[STAGED-INSTALL-OVERRIDE: no successor needed" in show.stdout


def test_ordinary_notes_without_staged_admission_are_unaffected(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)

    p = wq_close(env, tid, "--notes", "plain close, nothing staged", "--no-artifact", "t")
    assert p.returncode == 0, p.stdout + p.stderr
    assert tail1(p.stdout) == f"CLOSED wq#{tid}"
