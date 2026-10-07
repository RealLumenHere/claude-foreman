# wq#3595 — verify wq#3585 [ops-root-apply] wq3354-fleet-selfheal (install.sh --verify)

Verifier: claude-sonnet-5 (build worker, worktree `wq3595-a1`). Author of wq#3585: claude-sonnet-5.
Archetype 8 adversarial verify-pass. Verdict: **CONFIRM**.

## What wq#3585 claimed

wq#3585 was dispatched against the same `install.sh --verify` failure (4 `MISS` lines)
wq#3514's own closure report quoted. Its report
(`reports/wq3585_root-apply_wq3354-fleet-selfheal_verify-confirm.md`) states that gap no
longer existed by the time wq#3585 was picked up: wq#3584 (ticket number lower, landed
earlier, commit `80a50b13`) had already run the privileged install — code + five units +
timer enable/start + `selfheal.yaml` config merge — so wq#3585 did a read-only
re-verify instead of a root-apply, got `VERIFY OK`, and closed as a duplicate-dispatch
no-op (logged `kb/ISSUE-LOG.md` row 1209).

The actual diff landed by wq#3585 (commit `1a03461f`) is docs-only: one report file, 47
insertions, zero code/config changes. There is nothing in the diff itself that can be
"wrong" in the usual sense — the thing to adversarially check is whether its *claims about
live state* hold up.

## What I checked, independently of the claimed transcript

1. **Re-ran the probe fresh**, not trusting either wq#3585's or wq#3584's quoted output.
   Via `lumen-run start --name wq3595verify -- bash
   /home/lumen/foreman/ops/wq3354-fleet-selfheal/install.sh --verify` (unit
   `lumen-job-wq3595verify-1791384484`, timestamp later than both wq#3585's own run
   `...1791379923` and the independent wq#3593 verify's run `...1791383465`, so this is a
   third, later, distinct execution): `VERIFY OK`, `LUMEN_JOB_EXIT=0`.

2. **Did not take the script's own systemctl calls on faith** — ran `systemctl
   is-enabled`/`is-active` directly, outside the install.sh wrapper, on
   `lumen-exposure-census.timer`, `lumen-fleet-selfheal.timer`, `lumen-leakwatch.service`:
   enabled/enabled/(active service, no is-enabled needed) and active/active/active.
   Matches the report and matches wq#3593's prior independent confirmation of the same
   units.

3. **Re-ran the bundle's own test suite**: `python3 -m pytest -q
   ops/wq3354-fleet-selfheal/tests` → `56 passed`. This is the same gate `install.sh`
   itself runs before any copy/enable step (`install.sh:47-48`); a regression here would
   mean the shipped code no longer matches what's live, even if `--verify` can't see it.

4. **Independently read the live `selfheal.yaml`** (via the `Read` tool, not through
   install.sh, and not trusting either report's prose): confirmed `guards.timers` (line
   29) lists `lumen-exposure-census.timer`, and the full `exposure:` CLASS 8 block (lines
   116-137, `enabled: true`) is present. `install.sh --verify` itself only checks
   *existence* of `selfheal.yaml`, not this content — so this check closes a real gap the
   probe doesn't cover, rather than re-confirming something the probe already proved.

5. **Cross-checked the duplicate-dispatch narrative against ticket metadata directly**
   (`wq show`), not just the two reports' prose: wq#3584 `done_at 2026-10-07 13:28:27`
   precedes wq#3585 `claimed_at 2026-10-07 13:30:44` — wq#3584's root-apply had already
   landed and closed before wq#3585 was even picked up. `kb/ISSUE-LOG.md` row 1209 exists
   and its text matches the report's citation verbatim.

6. **Confirmed the probe can fail** (not a tautology): wq#3514's own closure report, which
   both wq#3584's and wq#3585's tickets quote verbatim, shows the identical probe
   returning 4 `MISS` lines pre-install. The `--verify` contract (`ops/_verify.sh`) exits 1
   on any `MISS` and 2 on an unreadable target — this is a real read-only check with a
   demonstrated failing branch, not a check that can only pass.

7. **Timer/service health beyond enabled+active**: `systemctl show
   lumen-exposure-census.timer -p LastTriggerUSec` is empty (daily timer, next elapse
   `2026-10-08 05:25:40 UTC`, hasn't fired on schedule yet — expected, not a defect) and
   `lumen-exposure-census.service`'s last recorded run shows `Result=success`,
   `ExecMainStatus=0`. Nothing here contradicts either report.

## Where I tried to break it

- Did not accept `VERIFY OK` from either prior transcript — ran the probe a third time,
  from a third session, and got the same result.
- Looked for the install.sh `--verify` contract being too shallow to catch a real gap
  (file-existence-only check on `selfheal.yaml`) and closed that gap myself by reading the
  live file's content directly — found the claimed config merge genuinely present, not
  just the file.
- Checked the duplicate-dispatch story wasn't a post-hoc excuse to skip real work, by
  comparing `wq show` timestamps for both tickets directly rather than trusting either
  ticket's narrative prose.
- Looked for drift between the repo's test suite and the live install (a stale "tests
  passed at install time" claim going stale) — re-ran them fresh, 56/56 still pass.

No contradiction found. Nothing in the live host, the merged config, the unit states, the
test suite, or ticket metadata disagrees with wq#3585's report.

## Verdict: CONFIRM

`ops_verified(wq3354-fleet-selfheal)` holds. wq#3585's claim — that no root-apply action
was needed because wq#3584 had already landed the identical install, and a read-only
re-verify was therefore the correct closure — is accurate on every independently-checked
axis: live `install.sh --verify` passes fresh (third independent run), the underlying
config merge is genuinely present (not just file-existence), the bundle's own test suite
still passes, and the duplicate-dispatch timeline is corroborated by raw ticket metadata,
not just narrative.
