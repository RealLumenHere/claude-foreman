#!/usr/bin/env python3
"""self_heal.py — wq blocked-ticket triage. wq#725.

wq#704/705 both exhausted MAX_ATTEMPTS and landed `blocked` not because the work was
impossible but because the spawn environment was broken (wrong --repo, a stale
worktree from a prior crashed attempt). Nothing distinguished that from a worker that
genuinely can't do the work, so both sat blocked until Frank noticed by hand.
fworker.handle_failure() (see fworker.block_reason_class) now tags every wq_block()
reason with a structured class — INFRA_CRASH / WORK_BLOCKED / LIMIT_HIT — plus a
blocked-at timestamp, packed into the `notes` field wq already stores. wq's own
schema has no block_reason/blocked_at columns (that schema lives in a different repo,
outside this one), so this reads the structured prefix back out of `notes` instead of
requiring a live migration on a shared, actively-claimed-against DB.

Only INFRA_CRASH is ever auto-healed: LAUNCH_FAILURE/KILLED/OOM mean the spawn
environment broke before real work happened, and releasing the claim lets the
dispatcher redispatch onto a fresh worktree — no human judgment needed. WORK_BLOCKED
(the worker decided it needs Frank) and LIMIT_HIT (it burned through its turn/token
budget) are never auto-released. A block with no structured prefix at all (e.g. a
ticket blocked by a human directly) is left alone the same way — no prefix means no
evidence it's environmental, and the conservative reading wins.

Per-ticket heal counts and stale/ceiling notifications live in a local JSON sidecar
(STATE_FILE) rather than wq, so a ticket that keeps re-blocking on the same fault
doesn't loop forever (MAX_HEALS) and a block older than STALE_AFTER_S gets escalated
to Frank instead of silently re-released forever.

Usage:
    self_heal.py sweep [--dry-run]           one pass, prints what it healed
    self_heal.py loop [--interval SEC]       repeat forever (lumen-run target)
"""
import argparse
import calendar
import json
import os
import re
import subprocess
import sys
import time

WQ_BIN = os.environ.get("FOREMAN_WQ_BIN", "/home/lumen/bin/wq")
NTFY_BIN = os.environ.get("FOREMAN_NTFY_BIN", "/home/lumen/bin/ntfy-send")
FOREMAN_ROOT = os.environ.get("FOREMAN_ROOT", "/home/lumen/foreman")
STATE_FILE = os.environ.get("FOREMAN_SELF_HEAL_STATE",
                             os.path.join(FOREMAN_ROOT, "self_heal_state.json"))

DEFAULT_INTERVAL_S = 600  # 10 min, per wq#725 acceptance criteria
STALE_AFTER_S = 30 * 60
MAX_HEALS = 5

REASON_RE = re.compile(
    r"^(?P<cls>INFRA_CRASH|WORK_BLOCKED|LIMIT_HIT)\[at=(?P<at>[^\]]+)\]: (?P<rest>.*)$")
LIST_ROW_RE = re.compile(r"^\[\s*(\d+)\]")


def log(msg):
    print(f"[self_heal {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {msg}", flush=True)


def wq(*args):
    return subprocess.run([WQ_BIN] + list(args), capture_output=True, text=True, timeout=30)


def wq_show(ticket_id):
    p = wq("show", str(ticket_id))
    out = {}
    for line in p.stdout.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    return out


def blocked_ticket_ids():
    p = wq("list", "--status", "blocked")
    if p.returncode != 0:
        log(f"wq list --status blocked failed rc={p.returncode}: {p.stdout.strip()} {p.stderr.strip()}")
        return []
    ids = []
    for line in p.stdout.splitlines():
        m = LIST_ROW_RE.match(line)
        if m:
            ids.append(int(m.group(1)))
    return ids


def ntfy(title, msg, priority="default"):
    try:
        subprocess.run([NTFY_BIN, "--title", title, "--priority", priority, msg],
                        capture_output=True, text=True, timeout=10)
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def parse_epoch(ts):
    return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))


def triage_one(ticket_id, state, dry_run):
    notes = wq_show(ticket_id).get("notes", "")
    m = REASON_RE.match(notes)
    if not m:
        log(f"wq#{ticket_id}: block reason has no structured class, leaving for a human "
            f"({notes[:80]!r})")
        return

    cls = m.group("cls")
    if cls != "INFRA_CRASH":
        log(f"wq#{ticket_id}: {cls}, never auto-healed")
        return

    try:
        blocked_epoch = parse_epoch(m.group("at"))
    except ValueError:
        log(f"wq#{ticket_id}: unparseable blocked-at {m.group('at')!r}, leaving for a human")
        return

    age_s = time.time() - blocked_epoch
    key = str(ticket_id)
    ticket_state = state.get(key, {"heals": 0, "notified": False})

    if age_s > STALE_AFTER_S:
        if not ticket_state["notified"]:
            log(f"wq#{ticket_id}: INFRA_CRASH block is {age_s / 60:.0f}m old (> "
                f"{STALE_AFTER_S / 60:.0f}m) — notifying Frank, leaving blocked")
            ntfy("foreman self-heal: stale INFRA_CRASH",
                 f"wq#{ticket_id} blocked {age_s / 60:.0f}m ago, not auto-healed: "
                 f"{m.group('rest')}", priority="high")
            ticket_state["notified"] = True
            state[key] = ticket_state
        return

    if ticket_state["heals"] >= MAX_HEALS:
        if not ticket_state["notified"]:
            log(f"wq#{ticket_id}: hit MAX_HEALS={MAX_HEALS} — notifying Frank, leaving blocked")
            ntfy("foreman self-heal: heal ceiling hit",
                 f"wq#{ticket_id} auto-healed {ticket_state['heals']} times and kept "
                 f"re-blocking — needs a human.", priority="high")
            ticket_state["notified"] = True
            state[key] = ticket_state
        return

    log(f"wq#{ticket_id}: INFRA_CRASH, {age_s / 60:.0f}m old, heal "
        f"{ticket_state['heals'] + 1}/{MAX_HEALS} — releasing")
    if dry_run:
        return
    p = wq("release", str(ticket_id))
    if p.returncode != 0:
        log(f"wq#{ticket_id}: wq release failed rc={p.returncode}: "
            f"{p.stdout.strip()} {p.stderr.strip()}")
        return
    ticket_state["heals"] += 1
    ticket_state["notified"] = False
    state[key] = ticket_state
    ntfy("foreman self-heal: auto-released",
         f"wq#{ticket_id} auto-released after INFRA_CRASH block "
         f"({ticket_state['heals']}/{MAX_HEALS} heals)")


def sweep(dry_run=False):
    state = load_state()
    ids = blocked_ticket_ids()
    if not ids:
        log("nothing blocked")
        return
    live = {str(i) for i in ids}
    for stale_key in [k for k in state if k not in live]:
        del state[stale_key]  # ticket left blocked (released/done/reclassified elsewhere)
    for ticket_id in ids:
        triage_one(ticket_id, state, dry_run)
    if not dry_run:
        save_state(state)


def cmd_sweep(args):
    sweep(dry_run=args.dry_run)
    return 0


def cmd_loop(args):
    log(f"loop starting: interval={args.interval}s")
    while True:
        try:
            sweep()
        except Exception as e:
            log(f"sweep failed (non-fatal, retrying next interval): {e}")
        time.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description="self_heal — wq blocked-ticket triage (wq#725)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_sweep = sub.add_parser("sweep", help="one pass over every blocked ticket")
    p_sweep.add_argument("--dry-run", action="store_true",
                          help="log what would happen, touch nothing")
    p_sweep.set_defaults(func=cmd_sweep)

    p_loop = sub.add_parser("loop", help="repeat sweep forever — the lumen-run target")
    p_loop.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    p_loop.set_defaults(func=cmd_loop)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
