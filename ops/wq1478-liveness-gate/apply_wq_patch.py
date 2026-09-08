#!/usr/bin/env python3
"""Idempotent patch for /home/lumen/bin/wq — Liveness Gate Slices 3-4 (wq#1478, LIVENESS_GATE.md §12.5).

Adds (1) `wq liveness-close <id> --verdict "..."`: the narrow close verb for a Frank-gated
ticket whose `Liveness:` premise is met — no claim needed (the user lane is never
agent-claimable by design, wq:1078, and that rule stays), refused unless the caller
identity base is liveness-clear and the ticket actually carries a Liveness line or a
ledger binding; (2) a creation check in cmd_add / cmd_decide that refuses to file or
queue a Frank-gate whose premise is already met on live state. Each block is guarded by
a marker so re-running is a no-op. Takes a timestamped .bak first.

    python3 apply_wq_patch.py [--wq /home/lumen/bin/wq] [--check]
"""
import argparse
import os
import shutil
import sys
import time

MARK_HELPERS = "# --- wq#1478 liveness gate helpers ---"
MARK_ADD = "# wq#1478: creation gate"
MARK_DECIDE = "# wq#1478: decide gate"
MARK_PARSER = "# wq#1478: liveness-close verb"

HELPERS = r'''
# --- wq#1478 liveness gate helpers ---
LIVENESS_HARNESS = os.environ.get("FOREMAN_HARNESS", "/home/lumen/foreman/harness")
LIVENESS_AGENT_BASES = ("liveness-clear", "liveness-clear-test")

def _liveness_module():
    """Lazy import of harness/liveness_clear.py. None when the harness is not there —
    every caller is fail-OPEN on None (a missing harness must never block filing a
    ticket, same shape as configured_lanes())."""
    try:
        sys.path.insert(0, LIVENESS_HARNESS)
        sys.path.insert(0, os.path.join(os.path.dirname(LIVENESS_HARNESS), "dispatcher"))
        import liveness_clear  # noqa: E402
        return liveness_clear
    except Exception as e:  # noqa: BLE001
        warn(f"liveness gate unavailable ({e.__class__.__name__}: {e}) — filing without it")
        return None

def _liveness_creation_check(title, detail, lane, frank_gated, force=False, verb="add"):
    """Refuse (exit 1) a Frank-gate whose premise is already met; warn on a tagless one.
    FOREMAN_LIVENESS_CLEAR=0 -> warn-only. --force files it with the verdict printed."""
    lc = _liveness_module()
    if lc is None:
        return
    try:
        ok, verdict, msg = lc.creation_check(title, detail, lane, frank_gated)
    except Exception as e:  # noqa: BLE001
        warn(f"liveness creation check crashed ({e!r}) — filing without it"); return
    if verdict == "NOT_GATED":
        return
    if ok:
        if verdict == "NO_TAG":
            warn(f"liveness: {msg}")
        elif msg:
            print(f"liveness: {verdict} — {msg}")
        return
    if force or os.environ.get("FOREMAN_LIVENESS_CLEAR", "1") == "0":
        warn(f"liveness {verdict} overridden ({'--force' if force else 'FOREMAN_LIVENESS_CLEAR=0'}): {msg}")
        return
    eprint(f"REFUSED: wq#1478 liveness gate — {verb} not done. {msg}")
    eprint("  A Frank-gate whose premise is already met on live state is the redo loop; fix the")
    eprint("  premise (or the Liveness: line) instead. --force files it anyway, on the record.")
    sys.exit(6)

def cmd_liveness_close(a):
    """wq#1478 Slice 3: close a Frank-gated ticket whose Liveness: premise is SATISFIED.
    No claim (user lane stays never-agent-claimable); instead THREE narrow guards:
    caller base must be liveness-clear, the ticket must carry a Liveness: line (or a
    ledger binding named on the call), status must be one that Frank would see.
    notes gets the probe verdict verbatim in the wq#759 [NO-ARTIFACT: ...] shape."""
    ping("liveness-close")
    caller, _ = resolve_identity(a)
    c = conn(); c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")
    base = base_of(c, caller) or caller
    if base.split("-")[0:2] not in [b.split("-")[0:2] for b in LIVENESS_AGENT_BASES]:
        c.execute("COMMIT"); c.close()
        eprint(f"REFUSED: liveness-close is for the liveness-clear identity only (caller {caller!r})")
        sys.exit(1)
    r = c.execute("SELECT status, detail, notes, lane FROM tasks WHERE id=?", (a.id,)).fetchone()
    if not r:
        c.execute("COMMIT"); c.close(); eprint(f"REFUSED: no such task {a.id}"); sys.exit(1)
    st, detail, prev_notes, lane = r
    if st == 'done':
        c.execute("COMMIT"); c.close(); print(f"already done wq#{a.id}"); return
    if st not in ('open', 'blocked', 'frank_gated', 'pending_review'):
        c.execute("COMMIT"); c.close()
        eprint(f"REFUSED: wq#{a.id} is {st!r} — liveness-close only acts on open/blocked/frank_gated/pending_review")
        sys.exit(1)
    has_line = "Liveness:" in (detail or "")
    if not has_line and not getattr(a, "binding", None):
        c.execute("COMMIT"); c.close()
        eprint(f"REFUSED: wq#{a.id} has no Liveness: line and no --binding was named — nothing proves the premise")
        sys.exit(1)
    verdict = (a.verdict or "").strip()
    if not verdict or "SATISFIED" not in verdict:
        c.execute("COMMIT"); c.close()
        eprint("REFUSED: --verdict must carry the probe's SATISFIED detail verbatim"); sys.exit(1)
    src = "Liveness: line" if has_line else f"ledger binding {a.binding}"
    stamped = f"[{now()}] [liveness auto-clear wq#1478] from {st} via {src} [NO-ARTIFACT: liveness: {verdict}]"
    notes = ((prev_notes + "\n") if prev_notes else "") + stamped
    clr = lease_clear_sql(c)
    c.execute(f"UPDATE tasks SET status='done', done_at=?, notes=?, claimed_by=?, lease_expires=NULL, "
              f"lease_ttl=NULL, block_reason=NULL, blocked_at=NULL, hold_by=NULL, hold_expires=NULL{clr} "
              f"WHERE id=?", (now(), notes, caller, a.id))
    c.execute("COMMIT"); c.close()
    print(f"liveness-closed wq#{a.id} ({lane}/{st} -> done): {verdict[:160]}")
# --- end wq#1478 helpers ---
'''

ADD_HOOK = '''    # wq#1478: creation gate — a Frank-gate whose premise is already met is not filed
    _liveness_creation_check(a.title, a.detail, a.lane, frank_gated, force=a.force, verb="add")
'''
DECIDE_HOOK = '''    # wq#1478: decide gate — queueing a question whose answer is on disk is the redo loop
    _lc_row = c.execute("SELECT title, detail, lane FROM tasks WHERE id=?", (a.id,)).fetchone()
    c.execute("COMMIT"); c.close()
    _liveness_creation_check(_lc_row[0], _lc_row[1], _lc_row[2], True, verb="decide")
    c = conn(); c.isolation_level = None
    c.execute("BEGIN IMMEDIATE")
'''
PARSER = '''# wq#1478: liveness-close verb
s=sub.add_parser("liveness-close",help="wq#1478: close a Frank-gated ticket whose Liveness: premise is SATISFIED on live state (liveness-clear identity only, no claim)"); s.add_argument("id",type=int); s.add_argument("--verdict",required=True,help="the probe verdict text, verbatim, must contain SATISFIED"); s.add_argument("--binding",default=None,help="ledger binding used when the ticket has no Liveness: line"); s.add_argument("--agent",default=None); s.set_defaults(f=cmd_liveness_close)
'''

ANCHOR_HELPERS = "def cmd_add(a):"
ANCHOR_ADD = "    needs_val = getattr(a, \"needs\", None)\n    if needs_val is not None:\n        check_needs_or_die(c, needs_val)  # wq#1331: refused pre-migration / on bad tags\n    c.execute(\"INSERT INTO tasks("
ANCHOR_DECIDE = "    prompt = (a.prompt or \"\").strip()\n    rec    = (a.rec or \"\").strip()\n"
ANCHOR_PARSER = "s=sub.add_parser(\"answer\""


def patch(src):
    changed = []
    if MARK_HELPERS not in src:
        assert src.count(ANCHOR_HELPERS) == 1, "cmd_add anchor not unique"
        src = src.replace(ANCHOR_HELPERS, HELPERS.lstrip("\n") + "\n" + ANCHOR_HELPERS)
        changed.append("helpers")
    if MARK_ADD not in src:
        assert src.count(ANCHOR_ADD) == 1, "cmd_add insert anchor not unique"
        src = src.replace(ANCHOR_ADD, ADD_HOOK + ANCHOR_ADD)
        changed.append("cmd_add hook")
    if MARK_DECIDE not in src:
        assert src.count(ANCHOR_DECIDE) == 1, "cmd_decide anchor not unique"
        src = src.replace(ANCHOR_DECIDE, ANCHOR_DECIDE + DECIDE_HOOK)
        changed.append("cmd_decide hook")
    if MARK_PARSER not in src:
        assert src.count(ANCHOR_PARSER) == 1, "parser anchor not unique"
        src = src.replace(ANCHOR_PARSER, PARSER + ANCHOR_PARSER)
        changed.append("parser")
    return src, changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--wq", default="/home/lumen/bin/wq")
    ap.add_argument("--check", action="store_true", help="report whether the patch is applied; write nothing")
    a = ap.parse_args(argv)
    src = open(a.wq).read()
    new, changed = patch(src)
    if a.check:
        print("applied" if not changed else f"missing: {', '.join(changed)}")
        return 0 if not changed else 1
    if not changed:
        print("already applied — nothing to do"); return 0
    bak = f"{a.wq}.bak.{time.strftime('%Y%m%d_%H%M%S')}_pre_wq1478"
    shutil.copy2(a.wq, bak)
    compile(new, a.wq, "exec")
    with open(a.wq, "w") as f:
        f.write(new)
    os.chmod(a.wq, 0o755)
    print(f"patched {a.wq}: {', '.join(changed)} (backup {bak})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
