# wq#2169 — verify of wq#2157 (56bfbfe, PR #720, retire disable-gpus)

Verdict: **CONFIRMED.** No contradiction. Verifier: claude-sonnet-5, cold read, 2026-09-20.

## Checks run

| Claim (PR #720 / `reports/2157_disable_gpus_retirement.md`) | Evidence | Result |
|---|---|---|
| 10 tests pass, no node contacted | `python3 ops/tests/test_wq2136_disable_gpus_verify.py` → `Ran 10 tests ... OK` | confirmed |
| `--verify` asserts retired state (not enabled / not active / not wanted / both D300s bound), bare run installs nothing, exits 3 | `install.sh:30-56`; `_verify.sh` supplies `_vmiss`/`_V_ERR`/`vdone`; test `test_no_install_path` | confirmed |
| probe capture-then-default is correct (`systemctl is-enabled` exits nonzero for a disabled unit) | local `systemctl is-enabled nonexistent-zz.service` → stdout `not-found`, rc 4; `test_probe_string_composes_and_emits_four_keys` runs the real one-liner | confirmed |
| hathor: `disabled`, `inactive`, not in `multi-user.target.wants/`, both D300s amdgpu-bound | `fleet-exec run hathor ...` → `disabled`, `inactive`, `grep -c disable-gpus` = 0, `0000:02:00.0` + `0000:06:00.0` | confirmed live |
| hathor at `dpm=auto`, not `low`, since 09-19 | `cat .../card1,card2/device/power_dpm_force_performance_level` → `auto`, `auto` | confirmed live |
| `gpu-lowpower.service` glob matched nothing on both 09-19 boots, `\|\| true` + `exit 0` hide it | `journalctl -u gpu-lowpower.service`: `card*/device/...: No such file or directory` at 09-19 07:48:41 and 15:13:43; ExecStart matches the report's description | confirmed live |
| RCA numbers (+16.6 W = 103.2 − 86.6) | `research/ISIS_POWEROFF_RCA.md:172-174` | confirmed |
| `run_readonly_cmds` at `fleet-exec-policy.yaml:180`, `sudo` verb unimplemented | line 180 ✓; the v2 note is at line 205, not 207 | confirmed, off by 2 (cosmetic) |
| MAC_POWER_DEPLOYMENT.md non-negotiable #2 rewritten | `Read` of `/home/lumen/MAC_POWER_DEPLOYMENT.md:8` shows the corrected text | spot-checked 1 of 6 sites |

## Not verified

- **nephthys live state**: `fleet-exec run nephthys` → `DENY [grant:no_ssh_run]`. The worker has no grant for it; this is a
  gap in the verifier, not evidence against the claim. The report's nephthys column (all `disabled`/`inactive`/bound, dpm `low`) is unchecked.
- **Blueprint edits, 5 of 6 sites**: the file is outside the repo, so it is not in PR #720's diff, and Bash `grep` on it is refused
  (path outside `--add-dir` roots). Only the `Read` spot-check above was possible.

## Observations (non-blocking)

- The RCA table (`research/ISIS_POWEROFF_RCA.md:172`) labels hathor `dpm=low` at 15:56Z on 09-20. The live state and the two 09-19
  boot failures say hathor was at `auto`, so that cell is wrong. The PR flags this in its section 3 and reasons that it strengthens the RCA;
  the RCA itself was not amended.
- `--verify` treats `is-active=failed` as retired. That is sound only because `enabled` and `wanted` are checked independently.
- The follow-up on the `gpu-lowpower.service` glob defect is a live power-control regression on hathor; it is worth a ticket of its own.
