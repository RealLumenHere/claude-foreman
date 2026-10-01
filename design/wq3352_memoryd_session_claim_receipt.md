# wq#3352 — memoryd rejects episode commit after claim release — root cause + fix

Status: RESOLVED. Code change landed in `lumen/minerva` (this foreman branch carries
the investigation and evidence; `harness/fworker.py`'s own commit ordering was
already correct — see "What was NOT the bug" below).

## Problem as filed

> wq#3304 and wq#3115 both hit this: episode commit written locally and spooled for
> replay because memoryd rejected the central write once the claim was released. The
> fworker harness releases the WQ claim before the episode commit, and memoryd
> requires an active claim.

## What was NOT the bug

`harness/fworker.py`'s own ordering is already correct: `do_attempt()` calls
`commit_episode_to_archive()` (fworker.py:3987) *before* `reconcile()` (fworker.py:4012,
which runs `wq_done()`), and every `wq_release()` fworker itself calls on a failure
path is reached only through `reconcile()`'s handlers — i.e. after the archival commit
already ran, every attempt. For the `fworker-wq<N>` identity specifically, a close
that happens *inside* the sandboxed session (the agent's own `wq done` call, which
precedes fworker's post-session archival commit) was already handled: wq#2901 made
memoryd's fworker branch check `kernel.ticket_claimant()` — the claim *receipt*
(`tasks.claimed_by`), which survives a `wq done` close — instead of the live lease.
wq#3309 (design, this repo)/wq#3315 (`lumen/minerva` PR#57, merged 2026-10-01 11:27Z)
then closed the remaining fworker-side gap: spool entries whose identity can never
re-authenticate (purged session, no source_ref, re-claimed ticket) now classify
terminal and land in `spool/recovered/` instead of retrying for 50h.

## What was the bug (confirmed live, 2026-10-01 ~16:00 UTC)

`v4/memoryd/daemon.py`'s `resolve_identity()` has two branches: one for
`fworker-wq<N>` identities (fixed by wq#2901/wq#3309), and a separate one for
ordinary **session** identities (chat sessions, and non-fworker fleet dispatchers —
e.g. the `burn-<N>-<hex>` identity scheme used by "fleet" lane workers, confirmed via
`wq show 3115`: `claimed_by: burn-3-fd1058`). The session branch never got the
wq#2901 fix — it still requires a *live* lease (`kernel.agent_claims()`, `WHERE
claimed_by=? AND status='claimed'`), which fails the instant the session runs `wq
done` on the very ticket it is about to commit an episode for. That is not an edge
case; it is the *normal* "finish the ticket, then commit the episode" ordering every
worker follows (see this repo's own protocol, e.g. `CLAUDE.md`'s "DONE IS A REPLAY"
section — call `wq done` before the episode/evidence step completes).

Live evidence, `journalctl -u lumen-episode-spool-replay.service` (pre-fix, 16:15:58Z):

```
20261001T102243561176Z-31904586.json: still-rejected (reject: agent does not hold a live claim on wq#3303)
20261001T135621172901Z-19581281.json: still-rejected (reject: agent does not hold a live claim on wq#3299)
20261001T140120281085Z-ce9b72db.json: still-rejected (reject: agent does not hold a live claim on wq#3298)
20261001T141334691562Z-0db195f6.json: still-rejected (reject: agent does not hold a live claim on wq#3115)
summary: recovered-local=1, still-rejected=6
```

wq#3115/show confirms `claimed_by: burn-3-fd1058`, `status: done` — the session
legitimately closed the ticket; `agent_claims()` says it no longer holds a *live*
lease, so every replay (up to 200 attempts / 14 days per wq#3173) rejects forever.
wq#3309's terminal-state check correctly does *not* recover these either: the
registry-purge/re-claim/no-source_ref classes it detects don't apply here — the
session is still registered and nobody re-claimed the ticket, so by that design a
future accept remains *possible* (it just never actually becomes possible, because
the session has moved on to other work and will not re-claim an already-closed
ticket).

## Fix

`lumen/minerva` PR#58 (merged `6301f75` → `fb441d4` on `main`), `v4/memoryd/daemon.py`
`resolve_identity()`: when the session branch's live-lease check
(`kernel.agent_claims()`) finds no live claim, fall back to
`kernel.ticket_claimant()` — the exact same "any status" claim-receipt check the
fworker branch has used since wq#2901 — before rejecting. Symmetric, minimal
(net +10 lines), no daemon/kernel schema change. New test file
`v4/memoryd/tests/test_session_claim_receipt.py` (4 tests, incl. a wq#2630 can-fail
probe: a ticket re-claimed by a *different* identity after the original session
closed it must still reject). `v4/memoryd/tests/` + `tests/test_episode_spool.py`:
52/52 pass; `tests/test_minerva_audit_chokepoint.py`: 24/24 pass (no regression).

Deployed and verified live (not just merged — "merged is not live",
`ops/VERIFY_CONTRACT.md`): `/home/lumen/minerva` fast-forwarded `ba336c2..fb441d4`,
`lumen-memoryd.service` restarted via
`/home/lumen/bin/lumen-run start --root -- systemctl restart lumen-memoryd`
(`/healthz` `uptime_s` dropped to `2.4`), then
`lumen-episode-spool-replay.service` triggered manually via the same `lumen-run`
path. Post-fix result, same spool, same run:

```
20261001T102243561176Z-31904586.json: landed (7f0e3c2b064a)      # wq#3303
20261001T140120281085Z-ce9b72db.json: landed (dd1051672fd3)      # wq#3298
20261001T141334691562Z-0db195f6.json: landed (f0dd4a1b6265)      # wq#3115
summary: landed=3, still-rejected=3
```

The three that still reject (`wq#3299`, and two `wq#3251` entries) are unaffected by
this fix by design: `wq#3299`'s session-branch reject means its ticket has since been
re-claimed by a different identity (provenance genuinely points elsewhere now — the
same wq#2630 can-fail case this fix's own test covers); the `wq#3251` entries are
`"does not hold the claim"` — the **fworker** branch's R4 message (daemon.py:138,
unrelated to the session branch this PR touches), already governed by wq#3309's
terminal-state design.

## Non-goals

- No change to the fworker branch, the registry check, or the live-lease
  computation for `agent_claims()` itself — only an added fallback path for the
  session branch's rejection, mirroring the pattern already trusted for fworker
  identities.
- No `harness/fworker.py` change: its own commit-before-release ordering was already
  correct (see "What was NOT the bug").
