# wq#3487 — close the gate for OPS tickets whose install.sh --verify fails

## Problem

A fix that needs a root install (`/etc/systemd/system`, `sudo`) ships as an
`ops/wqNNNN-*/install.sh` bundle because the fworker sandbox cannot do either.
wq#1724/wq#2762/wq#2943 built a real gate for this in `wq done` (bin repo):
when the cited evidence is (or a cited PR touches) an `ops/*/install.sh`
artifact, `cmd_done` calls `ops_verify_handler()`/`ops_deploy_check()` and
refuses (`rc=9`) unless the bundle is provably live.

That gate has a hole: **the override is never checked against reality.**
`--no-deploy-needed "<any text>"` and `--no-artifact "<any text>"` are
accepted as free text — nothing re-runs the bundle's own `--verify` to see
whether the override is true. Confirmed live today:

- `bin/tests/test_wq_ops_deploy_gate.py::test_ops_install_citation_with_no_deploy_needed_closes`
  and `bin/tests/test_wq2943_ops_deploy_url_gate.py::test_no_deploy_needed_remains_the_on_the_record_override`
  both close `rc=0` on a bundle whose `--verify` would fail, with zero call
  to `ops_verify_handler`.
- Path-citation closes (`kind=="path"`, the original wq#1724 shape) never
  call `ops_verify_handler` at all — only the byte-match `--deployed` proof
  is checked. Only the newer PR-URL branch (wq#2943) calls the handler, and
  only when no `--deployed`/`--no-deploy-needed` override is present.
- `--no-artifact` skips `find_artifact` entirely, so it skips
  `ops_deploy_check`/`pr_ops_deploy_check` wholesale — no bundle is even
  identified, let alone checked.

Real instance: wq#3336 (closed 2026-10-01, PR-URL citation) shipped a timer
bundle; the binary was live but the unit was never installed. Nobody's
override lied outright — the ticket just closed without the privileged half,
and wq#3359's verify-pass *noted* the gap but filed nothing. wq#3479 (filed
by a later, unrelated verify-pass on 2026-10-06) did the actual root install
five days later. Historical examples cited in the ticket (wq#901, wq#3386,
wq#1652, wq#1406) predate wq#1724/wq#2943 and show the same shape existed
before the gate too — closure notes saying "rollout pending" / "needs root"
with no follow-up ticket ever filed.

The class bug is: **closing an OPS ticket over a failing bundle is allowed,
on the record, and nothing turns that record into a tracked follow-up.** A
human has to notice the "rollout pending" text in a closed ticket's notes,
which is how wq#3479 happened — five days late, found by an unrelated
verify-pass, not by any system.

## What already exists (not rebuilt here)

- `ops/VERIFY_CONTRACT.md` (wq#1696): every bundle's `install.sh --verify`
  is read-only, exit 0 live / 1 MISS-lines / 2 can't-check. Already
  distinguishes **absent** from **differs** at the per-file level —
  `ops/_verify.sh:_vmiss` emits `"absent: $dst"` vs `"differs: $dst (from
  ...)"` — so requirement (2)'s "drift vs absent reported separately" is
  already true for every bundle that has a handler.
- `/home/lumen/bin/ops-deploy-drift` + `ops-deploy-drift.timer`
  (`systemctl list-timers` confirms it fires daily, `User=lumen`) already
  runs `--verify` on every bundle with a handler and opens one keyed aq row
  (`ops-drift:<bundle>`) per bundle that's been `VERIFY_FAIL` for >24h,
  auto-resolving it on the next `VERIFIED` run. Requirement (2)'s "scheduled
  sweep, one aq row per failing bundle" is already live infrastructure.
- Retiring a bundle today means hand-writing an inverted `--verify` handler
  that asserts the replacement is live and the retired unit stays absent
  (`wq858-trivy-weekly-scan`, `wq1299-phoenix-tracer`,
  `wq1396-minerva-proposal-promoter`, and — just-landed in the sibling
  ticket wq#3486 — `wq1406-paid-fallback-guard`, `wq2500-rsshub-bridge`).
  This works but is bespoke per bundle.

## Fix

### 1. `wq done`: no override escapes a confirmed `--verify` failure unlogged

Both close paths (`kind=="path"` citing `install.sh` directly, and
`kind=="url"` citing a PR that touches one or more bundles) now call
`ops_verify_handler()` on every identified bundle *before* honoring
`--no-deploy-needed`/`--no-artifact`/absence of `--deployed`. When the
handler says **`fail`** (not `none` — a handler-less bundle stays
unprovable either way, out of scope, same as today) and the bundle carries
no `RETIRED` marker (see below), `cmd_done`:

- auto-files (or reuses, if one is already open — matched by the same
  normalized-title dedup `cmd_add` already uses) a follow-up ticket titled
  `[ops-root-apply] <bundle>: install.sh --verify fails, needs privileged
  install`, in the same lane as the closing ticket, carrying
  `Liveness: ops_verified(<bundle>)` (the exact probe `harness/liveness.py`
  already wires up per `VERIFY_CONTRACT.md`'s "what consumes it" section) so
  the follow-up is machine-closeable the moment a root session actually
  runs the install — no human has to remember to look.
- stamps `[ROOT-APPLY-FILED: wq#<id>]` onto the closing ticket's notes.

This does **not** block the close. A sandboxed worker genuinely cannot do
root installs (Tier-0 instinct: that's a physical task, queue it, don't
gate on it) — refusing forever would just teach workers to find a worse
bypass. The fix is making the follow-up unconditional and automatic, so
"nothing files the follow-up" stops being true by construction.

`--no-artifact` additionally gets a narrow text scan (`OPS_INSTALL_RE`
against the ticket's own title+detail+notes, resolved locally the same way
`_local_bundle_installs` resolves a PR's changed files) so a worker can't
skip the whole bundle identification step just by omitting `--notes`
evidence for a ticket that is self-evidently about an ops bundle.

### 2. Retire marker

`ops/<bundle>/RETIRED` — a plain-text file (first line = reason, matching
the `# RETIRED <date> (wq#N, ...)` comment convention wq#3486 already used
inline in `install.sh`). Honored by:

- `ops-deploy-drift`: checked before `has_verify`/heuristic scanning. A
  marked bundle reports verdict `RETIRED`, never opens an aq row, and
  resolves any open `ops-drift:<bundle>` row the same way a `VERIFIED` run
  does.
- `wq done`'s new gate above: a marked bundle is treated as `none` (gate
  does not apply) regardless of what `--verify` says — no follow-up filed,
  no refusal.

This is strictly lighter than the inverted-handler pattern: dropping one
file retires a bundle without writing custom `--verify` logic. The
inverted-handler pattern remains correct for bundles that need to *actively
assert* a replacement is live (its sense is "I am retired AND my
replacement still works") — the marker only says "stop checking me,"
appropriate when nothing needs the obsolete bundle's absence enforced.

## Test to failure

`bin/tests/test_wq3487_ops_root_apply_followup.py` recreates the wq#3479
shape: a scratch repo with a PR-URL-cited `ops/wqNNNN-*/install.sh` bundle
whose `--verify` handler legitimately fails (the unit was never installed —
same shape `ops/_verify.sh`'s real handlers produce), closed with
`--no-deploy-needed "<reason>"`. Asserts:

- **Before the fix** (documented, not re-run — the pre-fix behavior is
  exactly today's `test_no_deploy_needed_remains_the_on_the_record_override`,
  which stays green and is extended, not replaced): the close succeeds with
  no trace that work remains.
- **After the fix**: the close still succeeds (`rc=0`, override honored,
  worker unblocked), but a new ticket titled
  `[ops-root-apply] wqNNNN-scratch-bundle: install.sh --verify fails, needs
  privileged install` exists in the scratch queue, carrying
  `Liveness: ops_verified(wqNNNN-scratch-bundle)`, and the closing ticket's
  notes carry `[ROOT-APPLY-FILED: wq#<id>]`.
- A second close of a second ticket citing the *same* still-failing bundle
  reuses the existing open follow-up (no duplicate ticket) — can-fail probe:
  a bundle with a *passing* handler must not get a follow-up at all, and a
  `RETIRED`-marked failing bundle must not either (drawn from outside the
  fix's target population, per wq#2630).
- `ops-deploy-drift --json` against the same scratch bundle layout reports
  `RETIRED` (not `VERIFY_FAIL`) once the marker file exists, and `--aq`
  resolves a pre-existing open `ops-drift:<bundle>` row instead of
  re-filing it.

## Repos touched

- `bin` repo (nested worktree under this branch's gitignored `_probe/`,
  landed as its own PR — see `wq3487_cross_repo_evidence.md` at this
  branch's root): `wq` (cmd_done gate), `ops-deploy-drift` (RETIRED
  verdict), new test file.
- This repo (`foreman`): this design doc, `ops/VERIFY_CONTRACT.md` update
  documenting the `RETIRED` marker as the lightweight alternative to an
  inverted handler.
