# wq#1779 — the dgm retirement still cannot be applied; the re-dispatch loop can be closed

## What this does not do

It does not retire `lumen-dgm.timer`. No lane in the fleet can. Re-verified live today:
the timer is still `enabled`, its fragment is `/etc/systemd/system/lumen-dgm.timer`
(root-owned, and `/etc` is outside a lane's allowed dirs), the unit-mutation verbs are
on the worker deny-list (`harness/worker-settings.json:107-109`), and `fleet-exec` still
has no root verb (`harness/fleet-exec-policy.yaml:205-211` marks `sudo_cmds` v2,
restart/reload/start only). wq#1779 stays blocked.

Cost of the wait, measured: DGM fired again at 17:00 UTC today (exit 0) and
`.dgm_history.jsonl` is at **101 lines** — 10 further DISCARD cycles since the 91 that
justified the retirement, 8 since ISSUE-LOG #378 recorded the wait at 93.

## What it does do

This ticket has now reached a BUILD lane five times (a1 blocked it, a2 was killed, a3 is
this one). ISSUE-LOG #377 and #378 named the walls but nothing stopped the dispatch,
because the cause was mechanical: **`capability_lint` had no detector for systemd unit
mutation.** `_V2_VERB_RE` (`harness/capability_lint.py:126`) covers
sudo/adb/fastboot/reboot/poweroff/shutdown/power-cycle/docker-restart; `_SYSTEMCTL_RE:114`
covers only the grantable restart|reload|start. So a ticket whose body says *"Build lanes
cannot mutate systemd … needs a lane with systemctl-mutate access"* derived
`tags=[] refused=[]` and dispatched clean, every time.

`_derive_unit_mutate()` reads that need and puts it in `refused`:

- the command shapes on the deny-list, and
- the prose self-declaration (`systemctl-mutate`, `mutate systemd`) — which is the shape
  all four stranded tickets actually took.

Unconditionally, with no advisory/prose split. That follows the `never.restart`
precedent at `capability_lint.py:667` rather than the v2-verb one: a v2 verb is waiting
on a broker grant that is planned, whereas unit mutation has no holder anywhere today,
so a prose mention of needing it is already disqualifying.

`refused` is consumed at `dispatcher/foreman_dispatcher.py:732` →
`handle_capability_skip(CAPABILITY_DENIED_BY_POLICY)`, **before** the slot is spent, and
the live mode is `apply` (enforcing).

## Evidence

- Before: `capability_lint.py --ticket 1779 --status claimed --json` → `refused: []`.
  After: two `unit-mutate(...)` entries.
- Corpus scan (203 tickets): exactly three flagged — wq#1739, wq#1762, wq#1779, the three
  stranded retirements. No healthy ticket newly refused.
- `pytest harness/tests/test_capability_lint.py` → 142 passed, including 8 new cases
  (five mutation shapes refused; restart/reload/start and verb-inside-a-name not refused).
  `dispatcher/tests/test_capability_replay.py` → 11 passed.

## Still open

The apply itself. The durable fix is an execution path for retirement work — a narrow
root grant, or a root-side runner handed
`ops/wq1778-dgm-retire-decision/remove-units.sh` — not a sixth follow-up ticket. The
runbook now says so, and points at applying it from a session that holds root via
detached `systemd-run`.

Refs: wq#1779, wq#1778, ISSUE-LOG #377/#378/#484.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
