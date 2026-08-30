#!/usr/bin/env python3
"""wq#1116 guards — end-to-end against the real /home/lumen/bin/wq on a throwaway DB.

Drives the CLI as a subprocess rather than importing it: the thing under test is the refusal a
second orchestrator actually sees at the chokepoint, including its exit code, and wq has no
importable surface (it runs argparse at module scope). WQ_DB points at a tempfile, so nothing
here can touch the live queue; WQ_DISPATCHER_CONFIG points at a path that does not exist so
configured_lanes() returns None and lane-vocabulary warnings stay out of the assertions.

Covers the three things the 2026-08-30 01:22-01:29Z double-orchestrator incident needed:
  A. owner_ok  — an unregistered caller may no longer write to another identity's claim,
                 except the one shape wq genuinely cannot distinguish (same-host host:pid).
  B. reclose   — `done` on an already-done ticket refuses instead of silently replacing notes.
  C. orch      — one orchestrator per lane-set, registration required, second driver told who.
Run: python3 test_wq1116_guards.py
"""
import os, subprocess, sys, tempfile

WQ = "/home/lumen/bin/wq"
FAILURES = []


def wq(*args, agent=None, strict=False, expect=0):
    """Run wq with the test DB. Returns combined stdout+stderr; asserts the exit code."""
    env = dict(os.environ)
    env["WQ_DB"] = DB
    env["WQ_DISPATCHER_CONFIG"] = "/nonexistent/wq1116-test-config.json"
    # Pin the grandfather cutoff into the past so every ticket these tests create is a guarded
    # claim; leaving it at the built-in default would make the suite pass or fail on the date.
    env["WQ_OWNER_STRICT_FROM"] = "2000-01-01 00:00:00"
    env.pop("WQ_AGENT", None)
    env.pop("WQ_STRICT", None)
    if agent:
        env["WQ_AGENT"] = agent
    if strict:
        env["WQ_STRICT"] = "1"
    p = subprocess.run([WQ, *args], env=env, capture_output=True, text=True)
    out = p.stdout + p.stderr
    if p.returncode != expect:
        FAILURES.append(f"wq {' '.join(args)} (agent={agent}) exited {p.returncode}, "
                        f"expected {expect}\n{out}")
    return out


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}")
        FAILURES.append(f"{label}: {detail}")


def new_ticket(title, holder, lane="auto"):
    """Add a ticket and claim THAT ticket as `holder`. Returns the ticket id.

    Claims by --id, and asserts the holder landed: claiming by lane silently hands back a
    different (or no) ticket once an earlier case has left one claimed, and a test whose ticket
    has claimed_by=NULL passes owner_ok's first early-out no matter what the guard does.
    """
    out = wq("add", "--lane", lane, "--title", title)
    tid = "".join(ch for ch in out.split("\n")[0] if ch.isdigit())
    wq("claim", holder, "--id", tid)
    shown = wq("show", tid)
    got = [l.split(":", 1)[1].strip() for l in shown.splitlines()
           if l.split(":", 1)[0].strip() == "claimed_by"]
    if got[:1] != [holder]:
        FAILURES.append(f"SETUP: #{tid} claimed_by={got}, expected {holder!r}\n{shown}")
    return tid


def registered(base):
    """Register a session and return the minted session id."""
    out = wq("whoami", "--register", base)
    line = [l for l in out.splitlines() if l.startswith("REGISTERED session:")][0]
    return line.split(":", 1)[1].strip()


def section(name):
    print(f"\n{name}")


with tempfile.TemporaryDirectory(prefix="wq1116-") as td:
    DB = os.path.join(td, "queue.db")

    section("A. owner_ok — unregistered non-holder writes")

    t = new_ticket("A1 named holder", "a1")
    out = wq("done", t, "--no-artifact", "test", agent="lumen:111", expect=1)
    check("A1 unregistered host:pid REFUSED against a named bare holder",
          "REFUSED" in out and "wq#1116" in out, out)

    # The incident's own shape: two drivers on one host, both anonymous as lumen:<pid>. An
    # earlier cut of this guard let it through on the grounds that fworker's claim-then-close
    # looked identical — which it did, until fworker started claiming under the worker's name.
    t = new_ticket("A2 same-host fallback pair", "lumen:222")
    out = wq("done", t, "--no-artifact", "test", agent="lumen:111", expect=1)
    check("A2 the incident's own shape (lumen:P1 held, lumen:P2 closing) REFUSED",
          "REFUSED" in out, out)

    t = new_ticket("A3 cross-host fallback", "lumen:222")
    out = wq("done", t, "--no-artifact", "test", agent="otherhost:111", expect=1)
    check("A3 host:pid from a DIFFERENT host REFUSED", "REFUSED" in out, out)

    t = new_ticket("A4 strict mode", "lumen:222")
    out = wq("done", t, "--no-artifact", "test", agent="lumen:111", strict=True, expect=1)
    check("A4 WQ_STRICT=1 refuses too", "REFUSED" in out, out)

    # The grandfather window, which exists only so the claims in flight at deploy could close.
    t = new_ticket("A4b pre-cutoff claim", "lumen:222")
    e = dict(os.environ)
    out = subprocess.run([WQ, "done", t, "--no-artifact", "test"],
                         env={**e, "WQ_DB": DB, "WQ_AGENT": "fworker-wq999-a1",
                              "WQ_DISPATCHER_CONFIG": "/nonexistent/x.json",
                              "WQ_OWNER_STRICT_FROM": "2099-01-01 00:00:00"},
                         capture_output=True, text=True)
    check("A4b a claim made BEFORE the cutoff still closes (deploy-window grandfather)",
          out.returncode == 0, out.stdout + out.stderr)

    t = new_ticket("A5 holder closes own", "lumen:222")
    out = wq("done", t, "--no-artifact", "test", agent="lumen:222")
    check("A5 the holder itself still closes cleanly", "done" in out and "REFUSED" not in out, out)

    sid = registered("a6")
    t = new_ticket("A6 registered non-holder", "someone-else")
    out = wq("done", t, "--no-artifact", "test", agent=sid, expect=1)
    check("A6 registered caller vs foreign holder still REFUSED (wq#502 regression)",
          "REFUSED" in out and "no excuse" in out, out)

    t = new_ticket("A7 read-only", "a7")
    out = wq("show", t, agent="lumen:111")
    check("A7 read-only `show` by a non-holder is untouched", "A7 read-only" in out, out)
    out = wq("list", agent="lumen:111")
    check("A7 read-only `list` by a non-holder is untouched", "REFUSED" not in out, out)

    t = new_ticket("A8 release", "a8")
    out = wq("release", t, agent="lumen:111", expect=1)
    check("A8 `release` by an unregistered non-holder REFUSED", "REFUSED" in out, out)
    out = wq("block", t, "why", agent="lumen:111", expect=1)
    check("A8 `block` by an unregistered non-holder REFUSED", "REFUSED" in out, out)

    # The dispatcher self-heal daemon releases INFRA_CRASH-blocked tickets it never claimed, as
    # an unregistered subprocess. `block` drops the lease, so nobody is holding those — refusing
    # them would have protected no one and stalled healing on every registered-held ticket.
    t = new_ticket("A9 self-heal path", "a9-worker")
    wq("block", t, "INFRA_CRASH", agent="a9-worker")
    out = wq("release", t, agent="lumen:111")
    check("A9 unregistered caller MAY release a blocked (lease-less) foreign ticket",
          "not a guarded live claim" in out and "released" in out, out)

    sid = registered("a10")
    t = new_ticket("A10 registered vs lease-less", "someone-else")
    wq("block", t, "INFRA_CRASH", agent="someone-else")
    out = wq("release", t, agent=sid, expect=1)
    check("A10 a REGISTERED caller is still refused, lease or no lease (wq#502 unchanged)",
          "REFUSED" in out, out)

    section("B. reclose — notes of an already-done ticket")

    t = new_ticket("B1 double close", "lumen:222")
    wq("done", t, "--notes", "FIRST close evidence", "--no-artifact", "b1", agent="lumen:222")
    out = wq("done", t, "--notes", "SECOND", "--no-artifact", "b1", agent="lumen:333", expect=4)
    check("B1 second close REFUSED with exit 4", "already done" in out, out)
    check("B1 refusal quotes the notes it would have destroyed", "FIRST close evidence" in out, out)
    out = wq("show", t)
    check("B1 the original notes survived the refused overwrite", "FIRST close evidence" in out, out)

    out = wq("done", t, "--notes", "SECOND", "--no-artifact", "b1",
             "--reclose", "first close cited the wrong PR", agent="lumen:333")
    check("B1 --reclose lets it through on the record", "done" in out, out)
    out = wq("show", t)
    check("B1 --reclose reason is recorded on the ticket",
          "RECLOSE: first close cited the wrong PR" in out, out)

    t = new_ticket("B2 reopen path", "lumen:222")
    wq("done", t, "--no-artifact", "b2", agent="lumen:222")
    out = wq("release", t, agent="lumen:222")
    check("B2 release of a done ticket is still the wq#841 reopen path, not a reclose",
          "REOPENED" in out, out)

    section("C. orch — one orchestrator per lane-set")

    out = wq("orch", "--acquire", "build", agent="lumen:111", expect=2)
    check("C1 unregistered identity REFUSED a lease", "not a registered session" in out, out)
    check("C1 refusal names the registration it wants", "whoami --register orch-" in out, out)

    orch_a = registered("orch-alpha")
    out = wq("orch", "--acquire", "build,auto", agent=orch_a)
    check("C2 registered orchestrator takes the lane-set", "holds orchestrator lease" in out, out)

    orch_b = registered("orch-beta")
    out = wq("orch", "--acquire", "auto", agent=orch_b, expect=2)
    check("C3 second orchestrator REFUSED on an overlapping lane", "REFUSED" in out, out)
    check("C3 refusal names the incumbent and since when",
          orch_a in out and "active since" in out and "lanes auto,build" in out, out)

    out = wq("orch", "--acquire", "github", agent=orch_b)
    check("C4 a DISJOINT lane-set is granted", "holds orchestrator lease" in out, out)

    out = wq("orch", agent="lumen:111")
    check("C5 `wq orch` lists both drivers to anyone, no registration needed",
          orch_a in out and orch_b in out and "2 orchestrator(s)" in out, out)

    out = wq("orch", "--release", agent=orch_a)
    check("C6 release drops the lease", "released orchestrator lease on auto,build" in out, out)
    out = wq("orch", "--acquire", "auto", agent=orch_b)
    check("C6 the freed lane is then available", "holds orchestrator lease" in out, out)

    out = wq("retire", orch_b)
    out = wq("orch", agent="lumen:111")
    check("C7 retiring an orchestrator drops its leases (wq#568 sweep covers orch: scopes)",
          "no orchestrator holds" in out, out)

    section("D. replay of the 2026-08-30 01:22-01:29Z incident")

    # Two claude.ai orchestrator sessions rode one Commander, both unregistered as lumen:<pid>.
    # B closed wq#1096 at 01:27:18 while A was still verifying it; A's later `wq done 1096`
    # replaced B's note with its own. Replayed with the same identities the incident had.
    a_sess, b_sess = "lumen:41001", "lumen:41002"
    t = new_ticket("D wq#1096 analogue", a_sess)
    out = wq("done", t, "--notes", "B: closed, PR#165 landed", "--no-artifact", "d",
             agent=b_sess, expect=1)
    check("D0 session B never closes A's live claim in the first place", "REFUSED" in out, out)

    # And if B had been the holder, A's later close still cannot erase B's evidence — the two
    # guards are independent on purpose, because either one alone leaves the incident half-open.
    t = new_ticket("D wq#1096 notes", b_sess)
    wq("done", t, "--notes", "B: closed, PR#165 landed", "--no-artifact", "d", agent=b_sess)
    out = wq("done", t, "--notes", "A: verified, closing", "--no-artifact", "d",
             agent=a_sess, expect=4)
    check("D1 the note-overwrite half of the incident is now REFUSED", "already done" in out, out)
    out = wq("show", t)
    check("D2 B's note survived, which is what was lost on wq#1096",
          "B: closed, PR#165 landed" in out and "A: verified" not in out, out)

    # And the half identity cannot catch is caught a step earlier: B never gets to dispatch,
    # because A holds the lane-set. This is the sentence that was missing at 01:22:44Z.
    orch_1 = registered("orch-session-a")
    wq("orch", "--acquire", "build,auto", agent=orch_1)
    out = wq("orch", "--acquire", "build", agent="lumen:41002", expect=2)
    check("D3 the second session is stopped before it spawns workers",
          "not a registered session" in out, out)
    orch_2 = registered("orch-session-b")
    out = wq("orch", "--acquire", "build", agent=orch_2, expect=2)
    check("D4 even registered, the second orchestrator is refused A's lane-set",
          orch_1 in out and "active since" in out, out)

print()
if FAILURES:
    print(f"FAILED — {len(FAILURES)} problem(s):")
    for f in FAILURES:
        print(f"\n--- {f}")
    sys.exit(1)
print("wq#1116: all guards pass.")
