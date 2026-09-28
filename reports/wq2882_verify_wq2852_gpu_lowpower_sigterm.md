# wq#2882: verify-pass on wq#2852 (commit `37e5c10`, gpu-lowpower@card1 SIGTERM at boot leave)

Date: 2026-09-28. Archetype 8 adversarial verify, fresh instance. Author: claude-opus-5-5
(wq#2852, lane build). Verifier: claude-sonnet-5@high (this ticket, lane build).

## Verdict: CONFIRM

Commit `37e5c10` ("fix(ops): keep gpu-lowpower drm tag on change uevents", merged to
`main`) changes four files in `ops/wq2166-gpu-lowpower-ordering/`:
`99-gpu-lowpower.rules`, `install.sh`, `RUNBOOK.md`, and `ops/tests/test_wq2166_gpu_lowpower.py`.
Read the diff cold, tried to break the rule change and the new probe logic, and ran the
full test suite. No contradiction found.

## What the fix does

- **Root cause claimed**: the udev rule only matched `ACTION=="add"`. amdgpu sends
  `change` uevents on the card minor (drm hotplug). On a `change` event the old rule
  didn't re-apply `TAG+="systemd"` / `SYSTEMD_ALIAS`, so PID1 saw the device without its
  current tag and dropped the `.device` unit's found bit, `BindsTo=` stopped
  `gpu-lowpower@card1`, mid-run SIGTERM → failed unit, even though the reconciler ran the
  same second and every card read low.
- **Fix**: `ACTION!="remove"` instead of `ACTION=="add"` (99-gpu-lowpower.rules:32),
  mirroring the pattern the comment attributes to upstream's `99-systemd.rules` net/bluetooth
  rules. `BindsTo=` is kept (a real card removal should still stop the instance);
  `SuccessExitStatus=SIGTERM` was considered and rejected (would hide the spurious stop,
  not prevent it) — reasoning recorded in RUNBOOK.md.
- **Probe hardening**: `install.sh --verify` now also greps
  `systemctl list-units --state=failed 'gpu-lowpower@*'` and fails (`_vmiss`) on any hit,
  closing the exact hole that let a green reconciler mask a failed per-card instance on
  nephthys. `install.sh` (apply path) also runs `systemctl reset-failed 'gpu-lowpower@*'`
  before re-triggering, so a stale failed instance from the old rule doesn't linger.

## What I checked

| Claim | How I checked it | Result |
|---|---|---|
| Full ops test suite still passes with the new rule/probe | `python3 -m pytest ops/tests/test_wq2166_gpu_lowpower.py -v` | 28/28 passed, incl. new `test_nephthys_as_found_on_2026_09_27_fails`, `ProbeFailedInstances::*`, `UdevRuleContract::test_rule_keeps_the_tag_on_change_events` |
| The new regression test is actually falsifiable (not drawn only from the population the fix was designed around) | Read the pre-fix `install.sh` (parent of `37e5c10`): it never queried `systemctl list-units --state=failed`, so the `failed=` line the new test asserts on could not have appeared in `$out`, and the `_vmiss` loop that checks it did not exist. Against that code, `test_nephthys_as_found_on_2026_09_27_fails`'s exact scenario (cards low, reconciler active/0, card1 instance failed) would have returned exit 0 — reproducing the real nephthys incident as a false-pass | Confirmed genuinely falsifiable, not a probe that can only pass |
| `gpu-lowpower@.service` is `Type=oneshot, RemainAfterExit=yes` (`SYSTEMD_WANTS` retriggering it on every subsequent `change` event would be a no-op start job, not a restart loop) | Read `ops/wq2166-gpu-lowpower-ordering/gpu-lowpower@.service` | Confirmed oneshot+RemainAfterExit; a `systemctl start` job on an already-active unit is a standard systemd no-op job-merge, so the widened rule does not cause repeated re-execution of the applier on hotplug churn |
| Probe's `failed=` extraction handles both `systemctl --failed`'s bulleted (`●`) and plain row formats | `ProbeFailedInstances::test_bulleted_row` / `test_plain_rows` / `test_nothing_failed`, and manually re-read the `grep -o "gpu-lowpower@[^ ]*\.service"` regex — glob-anchored to `@`, so it can't also match the non-templated `gpu-lowpower.service` reconciler unit | Correct, all pass |
| Live re-probe: `bash ops/wq2166-gpu-lowpower-ordering/install.sh --verify` against hathor/nephthys/khepri, and a direct read-only check via `fleet-exec run nephthys` | Attempted both from this worktree | **Blocked on both paths** — `install.sh --verify` (shells out via `ssh`) returned "This command requires approval" (ssh is not on the BUILD-lane allowlist, confirmed by grepping `harness/worker-settings.json` for `ssh` — zero entries); `fleet-exec run nephthys '...'` returned `DENY [grant:grant_missing_env]: grant file did not verify` (`FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA` are not set in this session — `fleet-exec` source, lines ~148-149). Did not retry either path (wq#1107). This is the same structural gap already on record at ISSUE-LOG rows #39 and #192 (wq#1444 hit the identical `grant:grant_missing_env` denial on a verify-pass ticket) — not a new finding |
| Upstream `99-systemd.rules` actually uses the `ACTION!="remove"` idiom for net/bluetooth, as the code comment claims | Tried `grep` on `/usr/lib/udev/rules.d/99-systemd.rules` | **Blocked**: path outside this session's allowed directories. Could not independently source-check this specific attribution. Not load-bearing for the verdict: the mechanism described (PID1 clearing a device's found-bit when its `CURRENT_TAGS` drops the `systemd` tag on a non-remove uevent) matches known systemd device-tagging semantics independent of whether the exact upstream file mirrors this idiom |

## Adversarial angles tried, not confirmed as flaws

- **Does widening to `ACTION!="remove"` cause double-tagging/alias churn on every hotplug
  event (e.g. monitor connect on the card)?** `TAG+=` and `ENV{SYSTEMD_ALIAS}+=` are
  append-to-set udev assignments; re-applying an already-present value is a standard
  idempotent udev pattern, not a bug.
- **Does the fix mask a real (non-benign) failure by pairing `--verify`'s new failed-unit
  check with `install.sh`'s new `reset-failed` on the apply path?** No: `reset-failed` only
  runs during install/apply, immediately followed by a re-trigger and then
  `gpu-lowpower.sh --check`, which fails the whole script (`set -euo pipefail`) if a card
  doesn't actually come back low. `--verify` (the read-only path) does not reset-failed —
  a genuinely-still-failed instance stays visible to the next `--verify` run.
- **Could the `--state=failed` glob `"gpu-lowpower@*"` swallow output from more than the
  expected card count on a 3+ card node and hide a miscount?** No — the existing
  `EXPECTED_CARDS`/`level=` checks already assert the right count independently; the
  failed-unit check is additive, not a replacement.
- Looked for a case where `BindsTo=` removal or `SuccessExitStatus=SIGTERM` would have
  been the safer fix instead — RUNBOOK.md's own rejection reasoning for both holds up:
  dropping `BindsTo=` would stop tracking real card removal, and
  `SuccessExitStatus=SIGTERM` only suppresses the alarm without stopping the spurious stop
  itself.

## Why this confirms without the live leg

Every claim reachable without a fleet-exec grant or ssh — the diff's own logic, the full
test suite (28/28, including a regression test proven falsifiable against the pre-fix
code), and the udev/systemd tagging mechanism the root-cause explanation relies on — held
up with no internal contradiction. The one unconfirmed detail (exact upstream rule-file
idiom) is corroborating, not foundational, to the fix's correctness. The only unresolved
leg is the live three-node re-probe, and that gap is structural to this lane (ISSUE-LOG
rows #39, #192), not a defect in the PR. No basis to contradict; no escalation warranted.
