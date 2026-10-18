#!/usr/bin/env python3
"""oh-wq-runner — bridge wq tickets to OpenHands 1.8 (wq#4200).

Design: /home/lumen/scratch/research/openhands-autonomous.md §3.5; acceptance gates §3.7.
Derived from the live-verified driver /home/lumen/scratch/openhands-test/oh_run_task.py
(API calls verified 2026-10-18). Script only — run via `lumen-run start -- ...`;
NO service/timer (stabilization freeze).

Modes:
  shadow  — replay already-CLOSED tickets against OpenHands and score the agent's
            outcome against the recorded wq outcome. Read-only on the queue:
            never claims, never writes done/block. This is the ONLY mode allowed
            until the §3.7 gate passes.
  live    — claim/run/verify/land/close. REFUSED unless live-gate.json beside this
            script attests every §3.7 item (a)-(g) plus wq#4152 item 6 (T1-T8 on
            the 1.8 stack). Do not hand-edit that file to true without the runs.

Invariants (ticket wq#4200):
  * wq claim --id only; the FENCE a claim prints is passed to every later
    done/block/heartbeat; the runner NEVER exits with a claim still held.
  * Runner owns the bounds the server lacks (§3.3): wall-clock deadline and a
    max-cost cap read from metrics.accumulated_cost every poll.
  * Sandbox is ALWAYS reaped in a finally block via
    DELETE /api/v1/sandboxes?sandbox_id=<id>  (sandbox_id is a QUERY param —
    the bare path form returns 422).
  * `idle` and `waiting_for_confirmation` are terminal-needs-human. `idle` is
    confirmed on 2 consecutive polls to avoid the startup race (a conversation
    can report idle before the agent's first action lands).
  * Host-side verification of the ticket's acceptance command — the agent's
    `finish` is never trusted (§3.5 step 7). Fail closed.
  * Landing is host-side only: artifacts are pulled out of the sandbox via the
    file API and committed from a worktree with lumen-pr; the sandbox never
    holds Gitea credentials.
  * Any breach (deadline, cost, error/stuck, needs-human, verify fail) ends in
    `wq block` with the reason — never a silent release, never a left claim.

Never prints or stores session_api_key (scrubbed before any dump).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WQ = os.environ.get("OH_WQ_BIN", "/home/lumen/bin/wq")
LUMEN_PR = os.environ.get("OH_LUMEN_PR_BIN", "/home/lumen/bin/lumen-pr")
LIVE_GATE_PATH = os.path.join(HERE, "live-gate.json")
# §3.7 (a)-(g) + wq#4152 item 6. Every key must carry non-empty evidence text.
LIVE_GATE_KEYS = ("a", "b", "c", "d", "e", "f", "g", "wq4152_item6")

RUNNING, DONE, NEEDS_HUMAN, FAILED = "running", "done", "needs_human", "failed"
# idle only counts as terminal after IDLE_CONFIRM_POLLS consecutive sightings.
IDLE_CONFIRM_POLLS = 2


class GateError(Exception):
    pass


# ---------------------------------------------------------------- API client

class Api:
    def __init__(self, base=None):
        self.base = base or os.environ.get("OH_BASE", "http://127.0.0.1:3000")

    def call(self, method, path, body=None, timeout=30):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors="replace")[:600]
        except Exception as e:  # noqa: BLE001 — network layer, caller classifies
            return 0, f"{type(e).__name__}: {e}"


def scrub(o):
    if isinstance(o, dict):
        return {k: ("<redacted>" if k == "session_api_key" else scrub(v)) for k, v in o.items()}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    return o


def reap_url(sandbox_id):
    """DELETE path for a sandbox. sandbox_id is a QUERY param (path form -> 422)."""
    return "/api/v1/sandboxes?sandbox_id=" + urllib.parse.quote(str(sandbox_id), safe="")


def reap_sandbox(api, sandbox_id):
    if not sandbox_id:
        return None
    code, _ = api.call("DELETE", reap_url(sandbox_id), timeout=60)
    return code


# ---------------------------------------------------------------- state machine

def classify(execution_status, idle_streak):
    """Map an execution_status to a runner-terminal class.

    finished -> DONE; error/stuck -> FAILED;
    waiting_for_confirmation -> NEEDS_HUMAN immediately;
    idle -> NEEDS_HUMAN once idle_streak >= IDLE_CONFIRM_POLLS (startup race guard);
    everything else -> RUNNING.
    """
    if execution_status == "finished":
        return DONE
    if execution_status in ("error", "stuck"):
        return FAILED
    if execution_status == "waiting_for_confirmation":
        return NEEDS_HUMAN
    if execution_status == "idle" and idle_streak >= IDLE_CONFIRM_POLLS:
        return NEEDS_HUMAN
    return RUNNING


def breach(elapsed_s, max_seconds, cost, max_cost):
    """First breached bound, or None. Cost comes from metrics.accumulated_cost."""
    if elapsed_s >= max_seconds:
        return f"wall-clock deadline breached ({elapsed_s:.0f}s >= {max_seconds}s)"
    if cost is not None and max_cost is not None and cost >= max_cost:
        return f"cost cap breached (${cost:.4f} >= ${max_cost:.4f})"
    return None


def extract_cost(conversation):
    try:
        return float(conversation["metrics"]["accumulated_cost"])
    except (KeyError, TypeError, ValueError):
        return None


# ---------------------------------------------------------------- bounded run

def run_task(api, prompt, title, out_dir, max_seconds, max_cost,
             poll_s=3, sandbox_id=None, on_poll=None, _sleep=time.sleep):
    """Submit one task, poll to terminal/bound, ALWAYS reap the sandbox.

    Returns {"final": DONE|NEEDS_HUMAN|FAILED, "reason", "cost", "elapsed_s",
             "conversation_id", "sandbox_id", "events_file", "reap_status"}.
    """
    os.makedirs(out_dir, exist_ok=True)
    req = {"title": title,
           "initial_message": {"role": "user",
                               "content": [{"type": "text", "text": prompt}],
                               "run": True}}
    if sandbox_id:
        req["sandbox_id"] = sandbox_id
    code, task = api.call("POST", "/api/v1/app-conversations", req)
    if code != 200 or not isinstance(task, dict):
        return {"final": FAILED, "reason": f"start failed: HTTP {code} {str(task)[:200]}",
                "cost": None, "elapsed_s": 0.0, "conversation_id": None,
                "sandbox_id": sandbox_id, "events_file": None, "reap_status": None}

    t0 = time.time()
    tid = task["id"]
    cid = task.get("app_conversation_id")
    sbx = task.get("sandbox_id") or sandbox_id
    final, reason, cost = FAILED, "no terminal state observed", None
    idle_streak = 0
    try:
        while True:
            elapsed = time.time() - t0
            b = breach(elapsed, max_seconds, cost, max_cost)
            if b:
                final, reason = FAILED, b
                break
            _sleep(poll_s)
            if not cid:
                c, st = api.call("GET", "/api/v1/app-conversations/start-tasks?ids="
                                 + urllib.parse.quote(tid))
                if c == 200 and isinstance(st, list) and st and st[0]:
                    s = st[0]
                    cid = s.get("app_conversation_id") or cid
                    if s.get("status") == "ERROR":
                        final, reason = FAILED, f"start task ERROR: {str(s.get('detail'))[:200]}"
                        break
                continue
            c, cv = api.call("GET", "/api/v1/app-conversations?ids=" + urllib.parse.quote(cid))
            if c != 200 or not isinstance(cv, list) or not cv or not cv[0]:
                continue
            conv = cv[0]
            sbx = conv.get("sandbox_id") or sbx
            cost = extract_cost(conv) or cost
            es = conv.get("execution_status")
            idle_streak = idle_streak + 1 if es == "idle" else 0
            if on_poll:
                on_poll(es, cost)
            cls = classify(es, idle_streak)
            if cls != RUNNING:
                final = cls
                reason = {DONE: "agent finished",
                          NEEDS_HUMAN: f"terminal-needs-human: execution_status={es}",
                          FAILED: f"agent terminal failure: execution_status={es}"}[cls]
                break
    finally:
        result = {"final": final, "reason": reason, "cost": cost,
                  "elapsed_s": round(time.time() - t0, 1), "conversation_id": cid,
                  "sandbox_id": sbx, "events_file": None, "reap_status": None}
        try:
            if cid:
                result["events_file"] = _save_events(api, cid, out_dir)
        finally:
            # The reap is the one thing that must survive every other failure.
            result["reap_status"] = reap_sandbox(api, sbx)
        json.dump(scrub(result), open(os.path.join(out_dir, "summary.json"), "w"), indent=1)
    return result


def _save_events(api, cid, out_dir):
    events, page = [], None
    for _ in range(40):
        q = ("/api/v1/conversation/%s/events/search?limit=100" % cid
             + (("&page_id=" + urllib.parse.quote(page)) if page else ""))
        c, ev = api.call("GET", q, timeout=60)
        if c != 200 or not isinstance(ev, dict):
            break
        events += ev.get("items", [])
        page = ev.get("next_page_id")
        if not page:
            break
    path = os.path.join(out_dir, "events.json")
    json.dump(scrub(events), open(path, "w"), indent=1)
    return path


def fetch_file(api, cid, sandbox_path, dest):
    code, body = api.call("GET", f"/api/v1/app-conversations/{cid}/file?file_path="
                          + urllib.parse.quote(sandbox_path), timeout=60)
    if code == 200:
        with open(dest, "w") as f:
            f.write(body if isinstance(body, str) else json.dumps(body))
    return code


# ---------------------------------------------------------------- wq integration

def sh(cmd, timeout=120):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def parse_fence(claim_stdout):
    m = re.search(r"^FENCE=(\d+)$", claim_stdout, re.M)
    return int(m.group(1)) if m else None


def wq_claim(ticket_id, agent):
    rc, out, err = sh([WQ, "claim", "--id", str(ticket_id), "--by", agent])
    if rc != 0:
        raise GateError(f"wq claim --id {ticket_id} failed rc={rc}: {err.strip()[:300]}")
    fence = parse_fence(out)
    if fence is None:
        # A claim we cannot fence is a claim we cannot safely release later.
        sh([WQ, "release", str(ticket_id)])
        raise GateError(f"wq claim --id {ticket_id} printed no FENCE=; released, aborting")
    return fence


def wq_heartbeat(ticket_id, fence, agent):
    sh([WQ, "heartbeat", str(ticket_id), "--fence", str(fence), "--agent", agent])


def wq_done(ticket_id, fence, agent, notes):
    return sh([WQ, "done", str(ticket_id), "--fence", str(fence),
               "--agent", agent, "--notes", notes])


def wq_block(ticket_id, fence, agent, reason):
    return sh([WQ, "block", str(ticket_id), str(reason)[:500],
               "--fence", str(fence), "--agent", agent, "--accept-probe"])


def wq_show(ticket_id):
    rc, out, _ = sh([WQ, "show", str(ticket_id), "--json"])
    return json.loads(out) if rc == 0 and out.strip() else None


def build_prompt(ticket):
    """Ticket -> sandbox prompt. No credentials, no host paths the sandbox lacks."""
    lines = [f"Task (from ticket wq#{ticket['id']}): {ticket['title']}", "",
             ticket.get("detail") or "", "",
             "Work entirely inside /workspace. Write every produced file under",
             "/workspace/out/. When finished, print DONE and stop. Do not ask",
             "questions — if the task is ambiguous, state your assumption and act."]
    return "\n".join(lines)


# ---------------------------------------------------------------- host verify

def host_verify(verify_cmd, artifact_dir):
    """Run the ticket's acceptance command on the HOST against extracted artifacts.

    The agent's `finish` is never trusted (§3.5 step 7). {artifacts} in the
    command expands to the extracted-artifact dir. Fail closed: no command
    configured -> NOT verified.
    """
    if not verify_cmd:
        return False, "no acceptance command configured (fail closed)"
    cmd = verify_cmd.replace("{artifacts}", artifact_dir)
    try:
        rc, out, err = sh(["/bin/sh", "-c", cmd], timeout=300)
    except subprocess.TimeoutExpired:
        return False, "acceptance command timed out (300s)"
    tail = (out + err)[-400:]
    return rc == 0, f"rc={rc} {tail.strip()}"


# ---------------------------------------------------------------- live gate

def check_live_gate(path=LIVE_GATE_PATH):
    """Live claiming is refused until §3.7 (a)-(g) + wq#4152 item 6 are attested."""
    if not os.path.exists(path):
        raise GateError(f"live mode refused: {path} missing — shadow mode only "
                        "until §3.7 (a)-(g) and wq#4152 item 6 pass")
    gate = json.load(open(path))
    missing = [k for k in LIVE_GATE_KEYS
               if not (isinstance(gate.get(k), str) and gate[k].strip())]
    if missing:
        raise GateError("live mode refused: live-gate.json lacks evidence for: "
                        + ", ".join(missing))
    return gate


# ---------------------------------------------------------------- shadow mode

def score_shadow(ticket, result):
    """Score one replay against the recorded outcome. Closed tickets are the
    ground truth: status 'done' should map to an agent DONE; anything else
    (blocked etc.) should NOT come back DONE."""
    recorded_done = ticket.get("status") == "done"
    agent_done = result["final"] == DONE
    return {"ticket": ticket["id"], "recorded_status": ticket.get("status"),
            "agent_final": result["final"], "reason": result["reason"],
            "cost": result["cost"], "elapsed_s": result["elapsed_s"],
            "outcome_match": agent_done == recorded_done,
            "reap_status": result["reap_status"]}


def pick_closed_tickets(count, lane="build"):
    rc, out, err = sh([WQ, "list", lane, "--status", "done"])
    if rc != 0:
        raise GateError(f"wq list failed rc={rc}: {err.strip()[:200]}")
    ids = [int(m) for m in re.findall(r"^\s*(\d+)", out, re.M)]
    return ids[:count]


def cmd_shadow(a):
    api = Api(a.base)
    ids = [int(x) for x in a.ids] if a.ids else pick_closed_tickets(a.count, a.lane)
    if len(ids) < a.min_tickets:
        raise GateError(f"shadow replay needs >= {a.min_tickets} closed tickets, got {len(ids)}")
    rows, spend = [], 0.0
    for tid in ids:
        ticket = wq_show(tid)
        if not ticket or ticket.get("status") not in ("done", "blocked"):
            print(f"[shadow] wq#{tid}: skipped (not a closed ticket)", flush=True)
            continue
        if a.total_cost_ceiling and spend >= a.total_cost_ceiling:
            print(f"[shadow] total-spend ceiling ${a.total_cost_ceiling} hit; stopping", flush=True)
            break
        out_dir = os.path.join(a.out_dir, f"wq{tid}")
        print(f"[shadow] wq#{tid}: submitting (deadline {a.max_seconds}s, "
              f"cap ${a.max_cost})", flush=True)
        result = run_task(api, build_prompt(ticket), f"shadow wq#{tid}", out_dir,
                          a.max_seconds, a.max_cost, poll_s=a.poll_seconds)
        row = score_shadow(ticket, result)
        rows.append(row)
        spend += result["cost"] or 0.0
        print(f"[shadow] wq#{tid}: {row['agent_final']} match={row['outcome_match']} "
              f"cost=${row['cost']}", flush=True)
    report = {"mode": "shadow", "ran": len(rows), "total_cost": round(spend, 4),
              "matches": sum(1 for r in rows if r["outcome_match"]),
              "accuracy": (sum(1 for r in rows if r["outcome_match"]) / len(rows)) if rows else None,
              "rows": rows}
    path = os.path.join(a.out_dir, "shadow-report.json")
    os.makedirs(a.out_dir, exist_ok=True)
    json.dump(report, open(path, "w"), indent=1)
    print(f"SHADOW REPORT {path} accuracy={report['accuracy']} "
          f"({report['matches']}/{report['ran']}) spend=${report['total_cost']}", flush=True)
    return 0 if rows else 2


# ---------------------------------------------------------------- live mode

def land_artifacts(api, cid, mappings, worktree, ticket_id):
    """Pull sandbox files host-side and land via lumen-pr from a worktree.
    The sandbox never holds Gitea creds (§3.5 step 8). mappings:
    ['<sandbox_path>:<repo_relpath>', ...]. Returns PR url line from lumen-pr."""
    for m in mappings:
        sandbox_path, rel = m.split(":", 1)
        dest = os.path.join(worktree, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        code = fetch_file(api, cid, sandbox_path, dest)
        if code != 200:
            raise GateError(f"artifact fetch failed HTTP {code}: {sandbox_path}")
    rc, out, err = sh([LUMEN_PR, "commit", "-m",
                       f"feat(oh): wq#{ticket_id} via oh-wq-runner"], timeout=300)
    if rc != 0:
        raise GateError(f"lumen-pr commit failed rc={rc}: {err.strip()[:300]}")
    rc, out, err = sh([LUMEN_PR, "open"], timeout=300)
    if rc != 0:
        raise GateError(f"lumen-pr open failed rc={rc}: {err.strip()[:300]}")
    m = re.search(r"https?://\S+", out)
    return m.group(0) if m else out.strip()[:200]


def run_one_live(api, a, ticket_id, agent):
    """Claim -> run -> verify -> land -> done; ANY breach -> block.
    The claim NEVER outlives this function."""
    fence = wq_claim(ticket_id, agent)
    closed = False
    try:
        ticket = wq_show(ticket_id)
        out_dir = os.path.join(a.out_dir, f"wq{ticket_id}")
        hb = {"n": 0}

        def on_poll(es, cost):
            hb["n"] += 1
            if hb["n"] % 20 == 0:
                wq_heartbeat(ticket_id, fence, agent)

        result = run_task(api, build_prompt(ticket), f"oh-wq wq#{ticket_id}", out_dir,
                          a.max_seconds, a.max_cost, poll_s=a.poll_seconds, on_poll=on_poll)
        if result["final"] != DONE:
            wq_block(ticket_id, fence, agent,
                     f"oh-wq-runner: {result['final']}: {result['reason']}")
            closed = True
            return result
        ok, evidence = host_verify(a.verify_cmd, out_dir)
        if not ok:
            wq_block(ticket_id, fence, agent,
                     f"oh-wq-runner: agent finished but host verification failed: {evidence}")
            closed = True
            return result
        pr = land_artifacts(api, result["conversation_id"], a.artifact or [],
                            a.worktree, ticket_id) if a.artifact else None
        notes = (f"{pr or 'no artifact mapping'} — oh-wq-runner: host-verified "
                 f"({evidence[:150]}), cost=${result['cost']}")
        rc, _, err = wq_done(ticket_id, fence, agent, notes)
        if rc != 0:
            wq_block(ticket_id, fence, agent,
                     f"oh-wq-runner: wq done refused ({err.strip()[:200]})")
        closed = True
        return result
    finally:
        if not closed:
            # Crash path: never leave a claim (§3.5 step 2).
            wq_block(ticket_id, fence, agent,
                     "oh-wq-runner: runner crashed mid-ticket (finally-block guard)")


def cmd_live(a):
    check_live_gate()  # raises until §3.7 + wq#4152 item 6 are attested
    api = Api(a.base)
    agent = a.agent or "oh-wq-runner"
    results = []
    for tid in [int(x) for x in a.ids]:
        results.append(run_one_live(api, a, tid, agent))
    return 0 if all(r["final"] == DONE for r in results) else 2


# ---------------------------------------------------------------- cli

def main(argv=None):
    ap = argparse.ArgumentParser(prog="oh-wq-runner", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(s):
        s.add_argument("--base", default=None, help="OpenHands API base (default $OH_BASE)")
        s.add_argument("--out-dir", required=True)
        s.add_argument("--max-seconds", type=int, default=1200, help="wall-clock deadline per task")
        s.add_argument("--max-cost", type=float, default=0.50,
                       help="per-task cap on metrics.accumulated_cost (USD)")
        s.add_argument("--poll-seconds", type=int, default=3)

    s = sub.add_parser("shadow", help="replay closed tickets, score vs recorded outcomes")
    common(s)
    s.add_argument("--ids", nargs="*", default=None, help="explicit closed ticket ids")
    s.add_argument("--count", type=int, default=10, help="auto-pick N most recent done tickets")
    s.add_argument("--lane", default="build")
    s.add_argument("--min-tickets", type=int, default=10,
                   help="§3.7(f): shadow replay must cover >= 10 closed tickets")
    s.add_argument("--total-cost-ceiling", type=float, default=2.0)
    s.set_defaults(f=cmd_shadow)

    s = sub.add_parser("live", help="claim/run/verify/land/close — gated by live-gate.json")
    common(s)
    s.add_argument("--ids", nargs="+", required=True)
    s.add_argument("--agent", default=None)
    s.add_argument("--verify-cmd", default=None,
                   help="host-side acceptance command; {artifacts} expands to the run dir; "
                        "REQUIRED to close a ticket (fail closed without it)")
    s.add_argument("--artifact", action="append", default=None,
                   metavar="SANDBOX_PATH:REPO_RELPATH",
                   help="file to pull from the sandbox and land via lumen-pr")
    s.add_argument("--worktree", default=os.getcwd(),
                   help="worktree lumen-pr lands from (host-side, holds the Gitea creds)")
    s.set_defaults(f=cmd_live)

    a = ap.parse_args(argv)
    try:
        return a.f(a)
    except GateError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
