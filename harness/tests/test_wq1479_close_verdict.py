"""wq#1479 — a state-changing CLI verdict must be impossible to miss.

The incident (2026-09-07): an orchestrator ran `wq done --no-artifact` on the frank-cred
tickets wq#1133 and wq#1296, read `tail -1` of the output, and reported both closed. `wq
done` had refused with exit 1, and printed its REFUSED text on STDOUT, where the last line
of the explanation reads like an ordinary trailing note. Both tickets were still `blocked`,
`done_at` NULL.

Two halves, tested here:
  A. the wq patch (ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py) — close-path
     refusals go to stderr, so `tail -1` of stdout cannot show a refusal at all;
  B. harness/wq_close.py — claim-then-done, gated on every exit code, confirmed against
     the ticket row, and its last line is always the verdict.

Same subprocess-against-a-scratch-DB pattern as test_wq_reopened.py.
"""
import json
import os
import subprocess
import sys

WQ_BIN = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
WQ_CLOSE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "wq_close.py")


def make_env(tmp_path, agent="wq1479-test-agent"):
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


def new_ticket(env, title="wq1479 scratch ticket", lane="auto"):
    p = wq(env, "add", "--lane", lane, "--title", title)
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


# --------------------------------------------------------------------------- A. the wq patch

def test_evidence_gate_refusal_is_on_stderr_only(tmp_path):
    """`wq done` without an artifact — the shape the orchestrator actually ran."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "claim", "--id", tid)
    p = wq(env, "done", tid, "--notes", "nothing verifiable in this string")
    assert p.returncode != 0
    assert "REFUSED" in p.stderr
    assert "REFUSED" not in p.stdout


def test_tail_minus_one_of_a_refused_close_can_never_read_as_success(tmp_path):
    """The incident's exact read. Whatever `tail -1` of stdout is, it must not be the
    close's success line — and with the refusal on stderr, stdout is empty."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "claim", "--id", tid)
    p = wq(env, "done", tid, "--notes", "no artifact here")
    assert tail1(p.stdout) == ""
    assert f"done {tid}" not in p.stdout
    assert status_of(env, tid) != "done"


def test_foreign_live_claim_refusal_is_on_stderr(tmp_path):
    """owner_ok — the guard that stops one session closing another's ticket (wq#1116)."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    assert wq(env, "claim", "--id", tid, agent="holder-session").returncode == 0
    p = wq(env, "done", tid, "--no-artifact", "x", "--notes", "n", agent="other-session")
    assert p.returncode != 0
    assert "REFUSED" in p.stderr and "is held by" in p.stderr
    assert "REFUSED" not in p.stdout


def test_reclose_refusal_is_on_stderr(tmp_path):
    """A second `wq done` is refused to protect the first close's notes (wq#1116)."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "claim", "--id", tid)
    assert wq(env, "done", tid, "--notes", "first", "--no-artifact", "t").returncode == 0
    p = wq(env, "done", tid, "--notes", "second", "--no-artifact", "t")
    assert p.returncode == 4
    assert "already done" in p.stderr
    assert "REFUSED" not in p.stdout


def test_an_ordinary_close_still_says_so_on_stdout(tmp_path):
    """The patch moves refusals, not answers: a successful close is still stdout, rc 0."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "claim", "--id", tid)
    p = wq(env, "done", tid, "--notes", "ok", "--no-artifact", "t")
    assert p.returncode == 0
    assert tail1(p.stdout).startswith(f"done {tid}")
    assert status_of(env, tid) == "done"


# ------------------------------------------------------------------- B. the close wrapper

def test_close_unblocks_claims_and_closes(tmp_path):
    """The frank-gated shape: blocked, claimed by nobody. `wq claim --id` refuses a blocked
    ticket outright, so the wrapper has to unblock first — that sequence is the whole point."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "block", tid, "waiting on frank")
    assert status_of(env, tid) == "blocked"

    p = wq_close(env, tid, "--notes", "liveness: premise satisfied",
                 "--no-artifact", "liveness verdict")
    assert p.returncode == 0, p.stdout + p.stderr
    assert tail1(p.stdout) == f"CLOSED wq#{tid}"
    assert status_of(env, tid) == "done"


def test_close_verdict_is_the_last_line_when_it_refuses(tmp_path):
    """No --no-artifact: the wq#759 gate fires. The wrapper reports NOT-CLOSED, exits 1,
    and quotes the refusal — the three signals the orchestrator had none of."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    p = wq_close(env, tid, "--notes", "nothing verifiable in this string")
    assert p.returncode == 1
    last = tail1(p.stdout)
    assert last.startswith(f"NOT-CLOSED wq#{tid} step=done rc=2")
    assert "wq#759" in last
    assert status_of(env, tid) != "done"


def test_a_failed_close_leaves_a_blocked_ticket_blocked(tmp_path):
    """Unblocking to claim is a means, not a side effect. A wrapper that leaves a frank-gated
    ticket `open` after a failed close has quietly widened the queue."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    wq(env, "block", tid, "waiting on frank")
    p = wq_close(env, tid, "--notes", "no artifact")
    assert p.returncode == 1
    assert status_of(env, tid) == "blocked"


def test_close_refuses_to_take_another_sessions_live_claim(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)
    assert wq(env, "claim", "--id", tid, agent="holder-session").returncode == 0
    p = wq_close(env, tid, "--notes", "n", "--no-artifact", "x", agent="thief-session")
    assert p.returncode == 1
    assert tail1(p.stdout).startswith(f"NOT-CLOSED wq#{tid} step=release")
    assert "is held by" in tail1(p.stdout)
    assert status_of(env, tid) == "claimed"


def test_close_is_idempotent_on_an_already_done_ticket(tmp_path):
    """A second run must report the truth about the ticket (done), not relay wq's re-close
    refusal as a failure — that would be the same class of lie, pointing the other way."""
    env = make_env(tmp_path)
    tid = new_ticket(env)
    assert wq_close(env, tid, "--notes", "first", "--no-artifact", "t").returncode == 0
    p = wq_close(env, tid, "--notes", "second", "--no-artifact", "t")
    assert p.returncode == 0
    assert tail1(p.stdout) == f"CLOSED wq#{tid}"


def test_close_of_a_missing_ticket_is_not_closed(tmp_path):
    env = make_env(tmp_path)
    p = wq_close(env, "999999", "--notes", "n", "--no-artifact", "x")
    assert p.returncode == 1
    assert tail1(p.stdout).startswith("NOT-CLOSED wq#999999")


def test_json_verdict_carries_the_refusal(tmp_path):
    env = make_env(tmp_path)
    tid = new_ticket(env)
    p = wq_close(env, tid, "--notes", "no artifact", "--json")
    v = json.loads(tail1(p.stdout))
    assert v["closed"] is False and v["step"] == "done" and v["rc"] == 2
    assert "wq#759" in v["refusal"]
    assert v["verdict"].startswith(f"NOT-CLOSED wq#{tid}")


# ------------------------------------------------------------------------- C. the keeper

def test_the_live_wq_is_still_patched():
    """ops/wq841's lesson: `wq` is rewritten in place by whichever lane touches it next, and
    that erased the reopened_count patch six hours after it landed. This is the alarm."""
    ensure = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "ops", "wq1479-close-verdict",
        "ensure_wq_refuse_stderr.py")
    p = subprocess.run([sys.executable, ensure, "--check", "--json"],
                       capture_output=True, text=True)
    report = json.loads(p.stdout)
    assert report["patched"], (
        f"{WQ_BIN} lost the wq#1479 stderr patch — close-path refusals are back on stdout. "
        f"Repair: python3 {ensure} --apply\n{report['patches']}")
