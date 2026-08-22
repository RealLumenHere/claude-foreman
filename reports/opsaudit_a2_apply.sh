#!/usr/bin/env bash
# opsaudit_a2_apply.sh — apply Frank's answers to the A2 backlog audit (wq#752).
#
# Companion to reports/opsaudit_a2_frank_backlog.md. This script touches ONLY the
# items that report classifies as (b), (c) and (d) — never the 30 decisions in §2,
# which stay Frank's.
#
# Dry-run by default: prints every command and changes nothing. Pass --apply to act.
# Idempotent: each action checks the ticket's current state first, so a re-run after
# a partial or interrupted run converges instead of double-applying.
#
#   ./opsaudit_a2_apply.sh                                            # show the plan
#   ./opsaudit_a2_apply.sh --apply --close-moot --ungate --accept-rejects
#   ./opsaudit_a2_apply.sh --apply --accept-rejects --keep 455,457    # spare two
#
set -uo pipefail

WQ=/home/lumen/bin/wq
DB=/home/lumen/agents/work-queue/queue.db
REPORT=reports/opsaudit_a2_frank_backlog.md

APPLY=0 CLOSE_MOOT=0 UNGATE=0 ACCEPT_REJECTS=0 DISPATCH_B=0 REQUEUE_FAILURES=0
KEEP=""

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Flags:
  --apply              actually run the wq commands (default: dry run)
  --close-moot         (c) close/retarget the 10 stale or moot items
  --ungate             (d) un-gate the mis-laned and over-gated items
  --accept-rejects     Batch A: close the 12 proposal-build REJECT-backs
  --dispatch-b         (b) unblock the 9 items an agent can answer today
  --requeue-failures   requeue the 8 worker-failure tickets wearing a blocked badge
  --keep 455,457       exempt these ids from every action
  --all                equivalent to the five action flags above
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)            APPLY=1 ;;
    --close-moot)       CLOSE_MOOT=1 ;;
    --ungate)           UNGATE=1 ;;
    --accept-rejects)   ACCEPT_REJECTS=1 ;;
    --dispatch-b)       DISPATCH_B=1 ;;
    --requeue-failures) REQUEUE_FAILURES=1 ;;
    --all) CLOSE_MOOT=1; UNGATE=1; ACCEPT_REJECTS=1; DISPATCH_B=1; REQUEUE_FAILURES=1 ;;
    --keep)             KEEP="${2:-}"; shift ;;
    -h|--help)          usage; exit 0 ;;
    *) echo "unknown flag: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[ "$APPLY" = 1 ] || echo "=== DRY RUN — nothing will change. Add --apply to act. ==="

kept() { case ",${KEEP}," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
status_of() { sqlite3 "$DB" "select status from tasks where id=$1;" 2>/dev/null; }

run() {  # run <id> <human-reason> -- <wq args...>
  local id="$1" why="$2"; shift 3
  if kept "$id"; then echo "  wq#$id  SKIP (--keep)"; return; fi
  printf '  wq#%-4s %s\n' "$id" "$why"
  printf '         $ %s %s\n' "$WQ" "$*"
  [ "$APPLY" = 1 ] || return
  "$WQ" "$@" || echo "         !! wq#$id FAILED — left as-is, safe to re-run" >&2
}

# close <id> <reason> — idempotent: a ticket already done is left alone.
close() {
  local id="$1" reason="$2"
  [ "$(status_of "$id")" = "done" ] && { echo "  wq#$id  already done — skip"; return; }
  run "$id" "close: $reason" -- done "$id" \
      --notes "wq#752 A2 backlog audit: $reason. See $REPORT" \
      --no-artifact "moot/superseded — closed by A2 audit, no new artifact produced"
}

# unblock <id> <reason> [extra wq edit args...] — idempotent on status=open.
unblock() {
  local id="$1" reason="$2"; shift 2
  [ "$(status_of "$id")" = "open" ] && [ $# -eq 0 ] && { echo "  wq#$id  already open — skip"; return; }
  run "$id" "un-gate: $reason" -- edit "$id" --status open --gate '' "$@"
}

echo
echo "### Batch A — 12 proposal-build REJECT-backs (research-before-build found the premise dead)"
if [ "$ACCEPT_REJECTS" = 1 ]; then
  for id in 439 445 446 447 448 450 451 453 454 455 457 458; do
    close "$id" "Frank accepted the REJECT-back; disproof already in ticket notes"
  done
else
  echo "  (skipped — pass --accept-rejects)"
fi

echo
echo "### (c) stale or moot — the question was already answered or overtaken"
if [ "$CLOSE_MOOT" = 1 ]; then
  close 653 "premise dead: ALLOW lumen/minerva is on agent-scope.conf line 66, approved 2026-08-16"
  close 693 "research complete: docs/ROOTSPEC.md written; unblocks wq#694, wq#695"
  close 328 "children wq#341-347 deployed; power.lumennode.uk live and CF Access-gated"
  close 343 "superseded: TOU cost landed query-time in Grafana per wq#344"
  close 316 "escrow half done 2026-07-17; remaining half is verbatim wq#339"
  close 397 "superseded by wq#567 decision C (accept contended p95), 2026-08-14"
  close 108 "WATCH expired: next check was due 2026-07-22; TIER1 scout feeds now cover it"
  # Retargets: a decision was made but work remains, so these are re-framed, not closed.
  run 567 "retarget: decision C made 2026-08-14 — hardware-deferred, not Frank-blocked" -- \
      edit 567 --status open --gate '' \
      --title "[memv3] Pin embed rung on replacement hw (decision C made 2026-08-14; deferred until hw lands)"
  run 160 "retarget: procurement approved 2026-07-16 — only the order remains" -- \
      edit 160 --title "[MP61-fw] U1: ORDER SPI programmer/clip/spare chip (~\$15-30) — approved 2026-07-16"
  run 765 "retarget: its item 1 is delivered by $REPORT" -- \
      edit 765 --detail "Item 1 (decision sheet for the parked backlog) DELIVERED: $REPORT — 30 genuine decisions, ordered by cost of delay. Items 2-4 (A6 approval model, A7 rule verdicts, A14 synthesis) still pending their tickets."
else
  echo "  (skipped — pass --close-moot)"
fi

echo
echo "### (d) should never have been gated on Frank"
if [ "$UNGATE" = 1 ]; then
  unblock 306 "blocked by wq#305, not by Frank — dependency, not a decision" --deps 305
  unblock 349 "pip install on a bench host is reversible + single-unit (spec R3)"
  unblock 462 "retiring a systemd unit that targets a NIC dead since 2026-08-02 is reversible" --lane fleet
  unblock 657 "a --merge-method flag on lumen-pr is build work, not a decision" --lane build
  unblock 420 "build ticket mis-filed into the user lane; prompt already written" --lane build
  unblock 702 "pstack tranche-2 port is build work mis-filed into the user lane" --lane build
  unblock 603 "TURNS worker failure, already requeued — belongs to A1, not to Frank"
  unblock 764 "campaign dependency on A1/A5/A6/A7, not a Frank gate"
  unblock 363 "a WATCH with defined triggers was never a gate"
else
  echo "  (skipped — pass --ungate)"
fi

echo
echo "### (b) answerable from existing evidence by an agent today"
if [ "$DISPATCH_B" = 1 ]; then
  unblock 124 "its gate — the NVMe purchase — closed as wq#123"
  unblock 191 "style/position half already PASSED decisively; the rest is measurable"
  unblock 351 "the ticket's own text offers 'retarget to a Mac' — no Frank exception needed" --lane fleet
  unblock 402 "gate unmet because nobody started the soak; an agent can start it"
  unblock 510 "re-blocked pending a re-read of rabbit15's disproof — that is agent work"
  unblock 511 "re-blocked pending a re-read of rabbit15's disproof — that is agent work"
  unblock 554 "choosing the LPP_NAV_TRACE default is an agent call"
  unblock 600 "measurement done, premise confirmed, corpus already plug-keyed 13/13"
else
  echo "  (skipped — pass --dispatch-b)"
fi

echo
echo "### worker failures wearing a blocked badge (A1's problem, not Frank's)"
if [ "$REQUEUE_FAILURES" = 1 ]; then
  for id in 358 663 678 679 688 690 707; do
    unblock "$id" "FALSE_COMPLETE/KILLED, not a Frank gate — requeue under A1 ceilings"
  done
  run 428 "EPIC parent should never have been dispatchable — park as an umbrella" -- \
      edit 428 --status open --pri 5 \
      --title "[EPIC][DO NOT CLAIM] LPP multiplayer: orchestrator-vs-orchestrator over emulated GB link"
else
  echo "  (skipped — pass --requeue-failures)"
fi

echo
if [ "$APPLY" = 1 ]; then
  echo "=== applied. Re-run without --apply to confirm the queue converged. ==="
else
  echo "=== DRY RUN complete — nothing changed. ==="
fi
