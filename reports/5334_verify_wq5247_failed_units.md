# wq#5334 — verify of wq#5247 (PR #2786, merge f208b82df0) — ops/wq5247-failed-units

Verifier: fresh instance, read cold. **Verdict: CONFIRM**, with three non-blocking
findings (one real defect, filed as wq#5337). The bundle does what its README says:
it diagnoses each unit, acts only when it finds the evidence it was written for, and
otherwise stops with `UNFIXED` and the evidence printed. Root causes are honestly
marked unconfirmed (README.md:28-29, 49, 65-66).

## Replayed
- `python3 -m pytest -q ops/wq5247-failed-units/test_failed_units.py` → 22 passed.
- Adversarial probes, `python3 -m pytest -q reports/5334_verify_wq5247_probe_test.py`
  → 3 passed (they assert the behaviour found, see below).
- Live, Lumen: `systemctl show audit-rules.service` → `Result=success`,
  `ExecMainStatus=0`, last run 2026-11-22 15:17:43Z (augenrules --load exit 0);
  `systemctl list-units --failed` → 0 units. The Lumen third is **live-clear**
  (oneshot, RemainAfterExit=no → `inactive`, so `is-failed` ≠ failed). Who ran it at
  15:17:43 is not visible to this uid (likely the ops-root-apply lane, wq#5332).
- Live, maksoor/bes: **not reachable from this lane** — `fleet-exec run maksoor ...` →
  `DENY [grant:no_ssh_run]`; `bash .../install.sh --verify` is not on the worker Bash
  allowlist. Liveness of those two halves stays with wq#5332.

## Checked and holding
- augenrules mechanics (/usr/sbin/augenrules:85-109): header line + comment/blank
  stripping + verbatim (CR-stripped) rule copy, so `auditctl -R` line N indexes
  `/etc/audit/audit.rules` as `audit_rule_at` assumes, and the whole-line match in
  `audit_rule_sources`/`audit_disable_rule` (lib.sh:22-60) finds the origin. `-D/-b/-f/-e`
  lines are relocated but carry no watch path, so they fall to `UNFIXED` — safe.
- Immutable mode: augenrules exits 0 itself (`check_immutable`, :49-53), so the unit is
  not failed in that case either; no wrong edit.
- Backups `X.rules.bak.<ts>_wq5247` are not re-read: augenrules only takes `\.rules$` (:86).
- `--verify` is read-only and honours ops/_verify.sh: unreachable host → `_V_ERR` →
  exit 2, never a silent pass (_verify.sh:135-138, 182-186).
- Root re-exec + `runuser -u lumen ssh -i <key>` for the remote halves (install.sh:48-58).

## Findings (non-blocking)
1. **P1, defect (wq#5337).** `grubenv_state` (lib.sh:71-78) reads grubenv as the
   unescalated bes user (remote-bes.sh:15); a *read failure* returns `bad-header`, and
   remote-bes.sh:23-24 then backs up and `grub-editenv create`s a healthy env (wiping
   `saved_entry` etc.). Probe P1 reproduces it with a 0000-mode healthy fixture. Impact is
   low: stock Ubuntu grubenv is 0644 (Lumen: `-rw-r--r-- root 1024 /boot/grub/grubenv`)
   and a `.corrupt.bak.<ts>` copy is kept.
2. **bes root cause likely mis-attributed.** Prior art: Ubuntu LP#2156423 (2026) —
   `grub-initrd-fallback.service` was ordered after the renamed `grub-common.service`
   rather than `grub2-common.service`, so it races grubenv rewrites at boot and logs
   "invalid environment block" while the on-disk file stays fine. Lumen's unit already
   has `After=grub2-common.service` (the fixed package). If bes is on the unfixed package,
   remote-bes.sh correctly refuses (probe P3: healthy grubenv → `UNFIXED`, untouched), but
   the bundle cannot clear it; the fix is a grub2-common upgrade then reset-failed. In wq#5337.
3. **Fix gated on current state, not on cause.** remote-maksoor.sh:27-29 exits "nothing
   to do" when the unit is not failed *right now*, even with the backlight device absent
   (probe P2), so a reset-failed unit re-fails next boot and `--verify` (is-failed only)
   reads green between boots. Same shape for Lumen's audit half. Acceptable for a
   one-shot repair bundle; noted so a green `--verify` is not read as "root cause fixed".
