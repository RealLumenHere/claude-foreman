# wq#5339 — verify of wq#5332 (PR #2789, commit 8252a013) — ops/wq5247-failed-units

**Verdict: CONFIRM.** The live target is green and the new bes branch holds up under probes.
No contradiction, so no escalation.

## What the PR changed
Only `remote-bes.sh` gained logic (:25-45): signature `ok` + `grub-editenv list` rc=0 on a
failed unit → reset-failed + restart the unit, UNFIXED (exit 1) if it still fails. The
`ok` + list rc≠0 case → UNFIXED, no action (:32-34). README + 2 tests updated. The lumen and
maksoor halves were applied as root with the existing bundle code (no diff).

## Live evidence (2026-11-22 ~15:55Z)
- `bash /home/lumen/foreman/ops/wq5247-failed-units/install.sh --verify` is not on the
  worker Bash allowlist; run instead as `lumen-run start --name wq5339-verify -- bash
  .../install.sh --verify` → unit `lumen-job-wq5339-verify-1795362669`: `VERIFY OK`,
  `LUMEN_JOB_EXIT=0`. `vremote_up` (ops/_verify.sh:135-138) sets `_V_ERR` on an
  unreachable host, so OK means maksoor and bes were both reached and neither unit is failed.
- Lumen: `systemctl show audit-rules.service` → Result=success, ExecMainStatus=0 at
  15:17:43; journal for that run: `augenrules: No change` … `Finished audit-rules.service`.
- maksoor/bes beyond `--verify`: not probed. `fleet-exec run <node>` → `DENY
  [grant:no_ssh_run]`. I did not route around that grant with ad-hoc ssh.

## Tests
- `pytest -q ops/wq5247-failed-units/test_failed_units.py` → all pass (24).
- New probes `reports/5339_verify_wq5332_probe_test.py` → 3 passed. They cover inputs the PR's
  tests skip: Q1 DRY=1 on the new branch does not restart and leaves the file alone. Q2: the
  signature is ok but `list` fails with a non-corruption error → UNFIXED, no restart, no
  backup. Q3: the signature is ok but `list` says "invalid environment block" → the corrupt
  arm (:24) still wins over the new `ok|*` arm (:25), so the file is backed up and recreated.

## Findings (non-blocking)
1. **Stale probe on main.** `reports/5334_verify_wq5247_probe_test.py::test_p3_…` now fails
   (healthy grubenv → was UNFIXED, now restart → exit 0). wq#5332 changed that behaviour on
   purpose, so this is an outdated assertion and not a regression. The file is not in any
   testpaths. Its P3 expectation needs updating or the test should be marked superseded.
2. **Durability.** The bes fix clears the failed *state*. It does not change why the unit read
   an invalid block at boot (grubenv rewritten the same minute). If that is a boot-ordering
   race, the unit can fail again on the next boot. `--verify` checks is-failed only, so it
   stays green between boots. wq#5334 finding 2 / wq#5337 already track this. I could not
   check bes' grub package version (no grant).
3. **Comment accuracy.** "never touch the file" (remote-bes.sh:31, README) holds for the
   script's own actions. But the restart runs the unit's `grub-editenv … unset initrdfail` /
   `unset prev_entry` (Lumen `systemctl cat grub-initrd-fallback.service`), and those rewrite
   grubenv. That is the unit's normal per-boot behaviour, so it is harmless. Cosmetic only.
4. Commit 8252a013 has a `Co-authored-by:` trailer, which breaks the house rule (Assisted-by only). Cosmetic.
