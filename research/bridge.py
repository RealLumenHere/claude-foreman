#!/usr/bin/env python3
"""bridge — research proposal → wq bridge (design/wq3755 §4-§6).

submit: a proposal reaches Frank only if
  1. its evidence passes (refusals(): file exists, every schema field typed right, accepted
     backend on the proposal's arch, install_exit == 0, smoke_exit == 0, proposal_sha256
     re-hashes to the file's current bytes, evidence younger than 7 days);
  2. it repeats no rejection-ledger row (same sha or same title — ledger.py);
  3. it was not already submitted (same sha on a bridge ticket);
  4. the ISO week has room: 5 bridge tickets per week (§6). The 6th parks in
     briefs/overflow/ instead of reaching wq.
Then `wq add --frank-gated` files it and `wq decide` queues the question, so it sits in
pending_review for `wq answer` like every other Frank decision. No new status (§4).

After Frank answers: approve = `wq review <id> accept` (ticket opens into the build lane);
reject = `bridge.py reject <id> --class C`, which writes the ledger row from Frank's
answer and closes the ticket.

Usage:
  bridge.py submit <proposal.json> [--dry-run] [--db Q] [--evidence-dir D] [--accept-backend B ...]
  bridge.py reject <wq_id> --class C [--reason R] [--db Q]
  bridge.py constraints [--db Q] [-n 50]      # the brief's constraint block
Exit 0 = submitted / would submit / rejected, 1 = refused, 3 = parked in overflow.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger  # noqa: E402
from sandbox_gate import EVIDENCE_DIR, EVIDENCE_FIELDS  # noqa: E402

MAX_AGE = datetime.timedelta(days=7)
ACCEPTED_BACKENDS = ("container",)
WEEKLY_CAP = 5
OVERFLOW_DIR = os.path.join(HERE, "briefs", "overflow")
WQ = os.environ.get("WQ_BIN", "/home/lumen/bin/wq")
MARKER = "Research-bridge: proposal_sha256="
MARKER_RE = re.compile(re.escape(MARKER) + r"([0-9a-f]{64}) brief_id=(\S+)")
TITLE_PREFIX = "research: "
NO_PROBE = ("Frank's approve/reject of a sandbox-verified research proposal; "
            "no deterministic probe answers it (wq#3760)")


def refusals(proposal_path, evidence_dir=EVIDENCE_DIR, accepted=ACCEPTED_BACKENDS, now=None):
    """Every reason this proposal's evidence can't go to Frank; empty list means it can."""
    with open(proposal_path, "rb") as f:
        raw = f.read()
    try:
        prop = json.loads(raw)
        pid, arch = prop["id"], prop["arch"]
    except (ValueError, KeyError, TypeError):
        return ["proposal is not a JSON object with id and arch"]
    path = os.path.join(evidence_dir, f"{os.path.basename(str(pid))}.json")
    if not os.path.isfile(path):
        return [f"no evidence: {path}"]
    try:
        with open(path) as f:
            ev = json.load(f)
    except ValueError as e:
        return [f"evidence is not JSON: {e}"]
    reasons = [f"evidence field {k} missing or not {getattr(t, '__name__', t)}"
               for k, t in EVIDENCE_FIELDS.items()
               if k not in ev or isinstance(ev[k], bool) or not isinstance(ev[k], t)]
    if reasons:
        return reasons
    if ev.get("backend") not in accepted:
        reasons.append(f"backend {ev.get('backend')!r} not in {list(accepted)}")
    if ev["arch"] != arch:
        reasons.append(f"evidence arch {ev['arch']} != proposal arch {arch}")
    if ev["install_exit"] != 0:
        reasons.append(f"install_exit={ev['install_exit']}")
    if ev["smoke_exit"] != 0:
        reasons.append(f"smoke_exit={ev['smoke_exit']}")
    if ev["proposal_sha256"] != hashlib.sha256(raw).hexdigest():
        reasons.append("proposal_sha256 mismatch: proposal edited after gating")
    try:
        ts = datetime.datetime.strptime(ev["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
        if (now or datetime.datetime.now(datetime.timezone.utc)) - ts > MAX_AGE:
            reasons.append(f"evidence stale: ts {ev['ts']} older than {MAX_AGE.days} days")
    except (KeyError, TypeError, ValueError):
        reasons.append("evidence ts missing or unparseable")
    return reasons


def iso_week_bounds(now):
    """[Monday 00:00, next Monday 00:00) UTC around `now`, in wq's created_at format."""
    start = (now - datetime.timedelta(days=now.isocalendar()[2] - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return (start.strftime(ledger.FMT),
            (start + datetime.timedelta(days=7)).strftime(ledger.FMT))


def week_count(c, now):
    """§6: bridge-filed tickets created this ISO week, whatever their status since."""
    lo, hi = iso_week_bounds(now)
    return c.execute("SELECT COUNT(*) FROM tasks WHERE gate_type='frank_decision' "
                     "AND instr(detail, ?) > 0 AND created_at >= ? AND created_at < ?",
                     (MARKER, lo, hi)).fetchone()[0]


def submitted_as(c, sha):
    row = c.execute("SELECT id FROM tasks WHERE instr(detail, ?) > 0 ORDER BY id LIMIT 1",
                    (MARKER + sha,)).fetchone()
    return row[0] if row else None


@contextlib.contextmanager
def bridge_lock(db):
    """Serialise count→add so two bridges can't both take the week's last slot."""
    with open(db + ".research-bridge.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def ticket_detail(prop, sha, evidence_dir):
    brief = "".join(str(prop.get("brief_id") or "-").split()) or "-"
    return "\n".join([
        f"Sandbox-verified research proposal: {prop['title']}",
        "",
        "Proposal (verbatim, sha256-pinned):",
        json.dumps(prop, indent=2, sort_keys=True),
        "",
        f"Evidence: {os.path.abspath(os.path.join(evidence_dir, prop['id'] + '.json'))}",
        f"Trigger: {prop.get('trigger') or 'manual'}",
        "Approve: `wq review <id> accept` after `wq answer`. "
        "Reject: `foreman/research/bridge.py reject <id> --class <slug>`.",
        f"{MARKER}{sha} brief_id={brief}",
    ])


def _wq(db, *args):
    env = {**os.environ, "WQ_DB": db}
    return subprocess.run([WQ, *args], capture_output=True, text=True, env=env, timeout=120)


def submit(proposal_path, db=None, evidence_dir=EVIDENCE_DIR, accepted=ACCEPTED_BACKENDS,
           dry_run=False, now=None, overflow_dir=OVERFLOW_DIR):
    """Returns (exit_code, report dict)."""
    db = db or ledger.DB
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = {"proposal": proposal_path}
    reasons = refusals(proposal_path, evidence_dir, accepted, now)
    with open(proposal_path, "rb") as f:
        raw = f.read()
    sha = hashlib.sha256(raw).hexdigest()
    prop = json.loads(raw) if not reasons else {}
    if not reasons and not (isinstance(prop.get("title"), str) and prop["title"].strip()):
        reasons.append("proposal has no title")
    if reasons:
        return 1, {**out, "verdict": "refused", "reasons": reasons}
    c = ledger.connect(db)
    try:
        with bridge_lock(db):
            rid = ledger.hit(c, prop["title"], sha)
            if rid:
                reasons.append(f"rejection ledger row {rid} matches this proposal")
            prior = submitted_as(c, sha)
            if prior:
                reasons.append(f"already submitted as wq#{prior}")
            if reasons:
                return 1, {**out, "verdict": "refused", "reasons": reasons}
            n = week_count(c, now)
            if n >= WEEKLY_CAP:
                out.update(verdict="would_park" if dry_run else "parked", week_count=n,
                           reasons=[f"weekly cap {WEEKLY_CAP} reached ({n} this ISO week)"])
                if not dry_run:
                    os.makedirs(overflow_dir, exist_ok=True)
                    dst = os.path.join(overflow_dir, f"{prop['id']}.json")
                    shutil.copyfile(proposal_path, dst)
                    out["parked_at"] = dst
                return 3, out
            if dry_run:
                return 0, {**out, "verdict": "would_submit", "reasons": [], "week_count": n}
            title = TITLE_PREFIX + " ".join(prop["title"].split())
            r = _wq(db, "add", "--frank-gated", "--title", title,
                    "--detail", ticket_detail(prop, sha, evidence_dir), "--no-probe", NO_PROBE)
            m = re.search(r"added task (\d+)", r.stdout)
            if r.returncode or not m:
                return 1, {**out, "verdict": "refused",
                           "reasons": [f"wq add failed rc={r.returncode}: "
                                       f"{(r.stdout + r.stderr).strip()[-400:]}"]}
            tid = int(m.group(1))
    finally:
        c.close()
    prompt = (prop.get("decision_prompt")
              or f"Approve research proposal '{prop['title']}' for the build lane? (yes/no + why)")
    rec = (f"Sandbox evidence passed (install_exit=0, smoke_exit=0, arch {prop['arch']}); "
           "approve if the need it answers is still live.")
    # decide re-runs the liveness on-ramp and does not read add's [NO-PROBE:] line back.
    d = _wq(db, "decide", str(tid), "--prompt", prompt, "--rec", rec, "--no-probe", NO_PROBE)
    out.update(verdict="submitted", wq_id=tid, week_count=n + 1, reasons=[])
    if d.returncode:
        out["warning"] = f"filed but `wq decide` failed: {(d.stdout + d.stderr).strip()[-300:]}"
    return 0, out


def reject(tid, reject_class, reason=None, db=None):
    """Frank said no: write the ledger row, then close the ticket. Returns (exit, report)."""
    db = db or ledger.DB
    c = ledger.connect(db)
    try:
        row = c.execute("SELECT status, title, detail, frank_response FROM tasks WHERE id=?",
                        (tid,)).fetchone()
        if not row:
            return 1, {"wq_id": tid, "verdict": "refused", "reasons": ["no such ticket"]}
        status, title, detail, response = row
        m = MARKER_RE.search(detail or "")
        if not m:
            return 1, {"wq_id": tid, "verdict": "refused",
                       "reasons": ["not a research-bridge ticket (no marker line)"]}
        if status not in ("frank_gated", "pending_review"):
            return 1, {"wq_id": tid, "verdict": "refused",
                       "reasons": [f"status {status!r}: only frank_gated/pending_review"]}
        why = reason or response
        if not (why or "").strip():
            return 1, {"wq_id": tid, "verdict": "refused",
                       "reasons": ["no frank_response on the ticket and no --reason"]}
        sha, brief = m.group(1), m.group(2)
        clean = title[len(TITLE_PREFIX):] if title.startswith(TITLE_PREFIX) else title
        try:
            rid = ledger.record(c, sha, clean, reject_class, why,
                                None if brief == "-" else brief)
        except ValueError as e:
            return 1, {"wq_id": tid, "verdict": "refused", "reasons": [str(e)]}
    finally:
        c.close()
    out = {"wq_id": tid, "verdict": "rejected", "ledger_row": rid, "reasons": []}
    d = _wq(db, "done", str(tid), "--no-artifact",
            f"research proposal rejected by Frank ({reject_class}); rejection ledger row {rid}")
    if d.returncode:
        out["warning"] = f"ledger row written but `wq done` failed: " \
                         f"{(d.stdout + d.stderr).strip()[-300:]}"
        return 1, out
    return 0, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="verify evidence, ledger, cap; then wq add --frank-gated")
    s.add_argument("proposal")
    s.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    s.add_argument("--evidence-dir", default=EVIDENCE_DIR)
    s.add_argument("--overflow-dir", default=OVERFLOW_DIR)
    s.add_argument("--accept-backend", action="append",
                   help=f"override accepted backends (default {list(ACCEPTED_BACKENDS)})")
    r = sub.add_parser("reject", help="record Frank's rejection in the ledger, close the ticket")
    r.add_argument("wq_id", type=int)
    r.add_argument("--class", dest="reject_class", required=True,
                   help="slug, e.g. duplicate, out-of-scope, already-solved, wrong-arch")
    r.add_argument("--reason", help="default: the ticket's frank_response")
    k = sub.add_parser("constraints", help="print the brief constraint block")
    k.add_argument("-n", type=int, default=ledger.CONSTRAINT_ROWS)
    for p in (s, r, k):
        p.add_argument("--db", default=None, help="queue.db (default $WQ_DB or the live one)")
    a = ap.parse_args(argv)
    if a.cmd == "constraints":
        c = ledger.connect(a.db)
        print(ledger.constraint_block(c, a.n))
        c.close()
        return 0
    if a.cmd == "reject":
        code, out = reject(a.wq_id, a.reject_class, a.reason, a.db)
    else:
        code, out = submit(a.proposal, a.db, a.evidence_dir,
                           tuple(a.accept_backend or ACCEPTED_BACKENDS), a.dry_run,
                           overflow_dir=a.overflow_dir)
    print(json.dumps(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
