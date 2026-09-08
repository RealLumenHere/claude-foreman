# wq#1498 — retire on a per-ticket fworker identity poisons every future spawn

## What happened

`fworker.wq_identity(ticket_id)` (`harness/fworker.py:490`) mints the STABLE name
`fworker-wq<id>` — every attempt at a given ticket claims under the identical name, on
purpose (wq#1116: the name that claims and the name that closes must agree). That makes it
never a dead session, only ever a currently-idle one.

`owner_ok`'s refusal text did not know that. When a caller ran into a stale claim held under
`fworker-wq<id>`, the ONLY remedy it printed was `wq retire fworker-wq<id>`. Two orchestrator
sessions (orch-ticket-reconcile, orch-fleetops-broker) followed that advice within 3 minutes
of each other on 2026-09-08 — and `wq retire` plants a permanent `retired_sessions` row. The
NEXT fworker dispatched for the SAME ticket claims under that exact same name and hits the
STOP banner (`RETIRED-REFUSED:claim` for fworker-wq1278/1345/1387 at 06:24:49-56Z). 13
tickets were affected; the rows were deleted by hand as a bandaid, which is the state this
patch replaces with a real fix.

## The fix

Scoped to the shape `^fworker-wq\d+$` (`FWORKER_IDENTITY_RE`, env-overridable via
`WQ_FWORKER_IDENTITY_RE`):

| what | where |
|---|---|
| `wq retire fworker-wq<id>` refuses unless `--dead-worker` is also passed — the flag is the explicit "I mean to permanently kill this identity" confirmation | `cmd_retire` |
| `owner_ok`'s refusal text stops recommending retire for that shape; points at `wq release <id> --dead-worker` instead | `owner_ok` |
| `wq release <id> --dead-worker` overrides owner_ok's refusal ONLY once `holder_is_dead()` independently verifies the holder: no live `agent_sessions` row, and (for the fworker-wq<id> shape) no running `lumen-job-fworker-wq<id>-*` unit per `lumen-run list` — the same signal `harness/concurrency.py`'s own concurrency cap reads, so this doesn't invent a second liveness source | `_finish`, `holder_unit_running`, `holder_is_dead` |
| Every dead-worker release logs the holder it took the claim from to `agent_activity` on the record | `_finish` (`log_activity(c, caller, f"dead-worker-release:{holder}")`) |

An operator who really does mean to permanently retire a per-ticket identity (not just clear
a stale claim) still can — `wq retire fworker-wq<id> --dead-worker` — it is an escape hatch,
not a hard block.

```
python3 ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py --check --json
python3 ops/wq1498-dead-worker-release/ensure_wq_dead_worker_release.py --apply
python3 -m pytest harness/tests/test_wq1498_dead_worker_release.py
```

`--apply` is anchor-based and idempotent, same shape as
`ops/wq1479-close-verdict/ensure_wq_refuse_stderr.py`: it backs up, patches a candidate copy,
proves it end-to-end against a scratch queue and a stub `lumen-run` (retire refuses without
`--dead-worker`; `release --dead-worker` refuses while the stub reports the unit running,
succeeds and logs the holder once it doesn't), and only then writes the live binary — rolling
back if the live copy fails the same check. It refuses outright if an anchor no longer
matches (`wq` moved out from under it) rather than half-patching.

The anchors assume wq#1479's stderr patch is already live (`owner_ok`/`_finish` refusals go
through `eprint`, not `print`) — this patch was derived against that state. `--check` reports
`MISSING-ANCHOR` rather than corrupting the file if that assumption ever stops holding.

Same keeper note as wq1479/wq841: `wq` is rewritten in place by whichever lane touches it
next, so this needs re-applying, not just applying once — `--check` is the alarm that fires
when the live wq loses the patch.

**Superseded in part by wq#1539**: the `_finish` block this patch introduced called
`owner_ok()` before checking the `--dead-worker` override's own eligibility, so a fully
successful override still opened its stderr with `owner_ok`'s REFUSED banner.
`ops/wq1539-release-dead-worker-order/` reorders that check; `FINISH_HEAD_NEW` in
`ensure_wq_dead_worker_release.py` was updated in place to the post-wq#1539 text so this
script's own `--check` keeper stays meaningful (applying wq#1498 fresh now also carries the
wq#1539 fix). `owner_ok` itself is untouched by wq#1539.
