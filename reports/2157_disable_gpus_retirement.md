# wq#2157 — retire disable-gpus on hathor/nephthys, correct the Mac blueprint (wq#2140 P4)

2026-09-20 · grants: `ssh(hathor)`, `ssh(nephthys)`, verb `run` only.

## Verdict

Both ticket actions delivered, one with a named residual.

1. **Unit retired on hathor and nephthys — verified live.** Both nodes: `disabled`, `inactive`,
   absent from `multi-user.target.wants/`, both D300s amdgpu-bound. The unit cannot start at boot.
   The unit *file* is still on disk; deleting it needs a mutating remote verb that does not exist
   (below).
2. **Mac blueprint corrected** — `/home/lumen/MAC_POWER_DEPLOYMENT.md`, six sites.

Plus one live defect found while verifying the ticket's own premise: **hathor has not been at
`dpm=low` since 2026-09-19.**

## 1. Live state (read-only, `fleet-exec run`, 2026-09-20)

| Check | hathor | nephthys |
|---|---|---|
| `systemctl is-enabled disable-gpus.service` | `disabled` | `disabled` |
| `systemctl is-active disable-gpus.service` | `inactive` | `inactive` |
| in `/etc/systemd/system/multi-user.target.wants/` | no | no |
| `ls /sys/bus/pci/drivers/amdgpu/` | `0000:02:00.0`, `0000:06:00.0` | `0000:02:00.0`, `0000:06:00.0` |
| unit file | present, 376 B, Sep 19 09:09 | present, 376 B, Sep 19 09:09 |
| `gpu-lowpower.service` in `multi-user.target.wants/` | yes | yes |

The ticket's stated confirmation — "confirm neither node has it wanted by multi-user.target" — holds
on both nodes. Combined with `disabled` + `inactive`, the unit is functionally retired: there is no
path by which it starts.

**Residual — the unit file on disk.** Not removed. `fleet-exec run` is read-only
(`harness/fleet-exec-policy.yaml:180`, `run_readonly_cmds`), and the `sudo` verb that would allow
`rm`/`daemon-reload` is still v2/unimplemented (same file, line 207). The granted verbs were `run`
only. The file is inert while the unit is disabled and unwanted. Queued as a follow-up rather than
worked around.

## 2. Mac blueprint corrections (`/home/lumen/MAC_POWER_DEPLOYMENT.md`)

The file already carried the 2026-06-25 *CRITICAL REVERSAL* at line 271 — but ~200 lines **after**
the Phase 2 instructions that contradict it. A reader working top-down followed the wrong advice
and got the isis configuration. Corrected in place (edits are additive: original text is struck and
retained, nothing deleted, so the document stays a log):

| Site | Was | Now |
|---|---|---|
| Non-negotiable #2 (head of file) | "Both GPUs power-managed (one forced-low, one runtime-PM gated)" | both cards amdgpu-bound at `dpm=low`; points at both reversals |
| PHASE 2 heading | no warning | SUPERSEDED banner: the two measurements, the rule, and where to read them |
| PHASE 2 GPU-B role | "driver-unbound + runtime PM auto → D3hot" | marked HISTORICAL; GPU-B stays amdgpu-bound like GPU-A |
| PHASE 2 udev/vfio recipe | live `sudo tee` + `driver_override` steps | marked HISTORICAL, DO NOT APPLY; "never vfio a 6,1 GPU" |
| PHASE 2 savings estimate | "~12-17W/Mac" | never realized and **sign is wrong**: +16.6 W measured; ~78-80 W is the real idle |
| PHASE 4 step 4 | "confirm ... with GPU-B gated" | confirm both D300s amdgpu-bound at `dpm=low`, with the commands |
| ROLLOUT step 1 | "GPU-B-gating-vs-SSD validation happens here" | dropped — nothing to validate |
| 2026-06-12 DEFERRED list | "GPU-B driver_override unbind → D3hot" awaiting console | CANCELLED, not deferred |
| end of file | — | new dated section: the 2026-09-20 second reversal, measurements, retirement state |

Left alone deliberately: the wq#159 GRUB link-disable package (line ~363). Different mechanism
(pre-kernel, not post-boot), installed on no node, every step Frank-gated, and its own text already
carries the correct standing rule. Retiring it is a separate decision.

No backup file could be written next to the original (Bash is path-bounded to the worktree and
`--add-dir` roots; `cp` to `/home/lumen/` is refused). The edits were made additive for that reason.

## 3. Defect found: hathor has not been at dpm=low since 2026-09-19

Not part of this ticket — found while checking the premise that hathor is the healthy control.

```
hathor  card1/card2  power_dpm_force_performance_level = auto      (want low)
hathor  card1        power_dpm_state                   = balanced  (want battery)
nephthys card1/card2 power_dpm_force_performance_level = low       correct
hathor  systemctl is-active gpu-lowpower.service       = active
```

`gpu-lowpower.service` reports `active` and has applied nothing for a day. Its `ExecStart` globs
`/sys/class/drm/card*/device/...`; on both 09-19 boots the glob matched nothing and bash passed the
literal pattern through:

```
2026-09-19T07:48:41 hathor bash[1880]: /bin/bash: line 1:
  /sys/class/drm/card*/device/power_dpm_force_performance_level: No such file or directory
2026-09-19T15:13:43 hathor bash[1913]: (same)
```

The 2026-08-31 boot has no such error, so this is new. `Type=oneshot` plus `|| true` plus `exit 0`
make the failure invisible to `is-active` — the unit is green while the fleet's headline power
control is off. Filed as a follow-up.

**This does not weaken the RCA.** If anything it strengthens it: hathor's 86.6 W control was
measured while hathor was at `auto`/`balanced`, i.e. *not* in its low-power state, and isis still
lost to it by 16.6 W. The gap with hathor correctly at `dpm=low` should be wider.

## 4. Verification

`ops/wq2136-hathor-disable-gpus-unit/install.sh --verify` was rewritten: it used to assert the unit
was *installed* and matched the bundle; it now asserts the retired state (not enabled, not active,
not wanted, both D300s amdgpu-bound) and fails if a node drifts back toward disabled GPUs.

`python3 ops/tests/test_wq2136_disable_gpus_verify.py` — **10 tests, all pass**. Every case stubs
`ssh` on `PATH`; no node is contacted. Covers: retired state passes; deleted unit file
(`not-found`) passes; re-enabled fails; active fails; still-wanted fails; one GPU unbound fails;
both GPUs unbound reports all four; unreachable node exits 2 (never a false drift alert); the bare
invocation installs nothing and exits 3.

The tenth case executes the probe one-liner itself rather than faking its output, and caught a real
bug in the first draft: `$(systemctl is-enabled "$u" || echo absent)` corrupts its reading on the
happy path, because `systemctl is-enabled` **exits 1 for a disabled unit** — the fallback fires when
the answer is correct and the substitution captures both writes (`disabled\nabsent`). The same shape
also mis-reads a missing unit, which systemd reports as the string `not-found`, not as empty output.
Fixed by capturing first and defaulting only on empty.

Not run: the composed probe over ssh against a live node — `fleet-exec` rejects argv metacharacters
(`DENY [argv:argv_metachar]`), and bare `ssh` is not on the worker allowlist. Its four component
reads were each run against both nodes individually and produced the values above; the composition
itself is covered locally by the tenth test.

## 5. ISSUE-LOG rows — could not be appended, transplant needed

`Edit` on `/home/lumen/kb/ISSUE-LOG.md` is blocked: `guard.sh: BLOCKED — Write/Edit inside PRIMARY
tree /home/lumen/kb denied (wq#1719/wq#900)`. Same gap the wq#2145 verify hit. Rows to add:

| n | symptom | root cause | solution | status |
|---|---|---|---|---|
| 338 | wq#2157 "remove the unit" could not be completed — no reachable way to `rm` a file or `daemon-reload` on a remote node | `fleet-exec run` is read-only (`harness/fleet-exec-policy.yaml:180`); the `sudo` verb that would cover it is v2/unimplemented (line 207); granted verbs were `run` only | delivered the functional retirement instead and verified it live (disabled + inactive + unwanted + both GPUs bound = cannot start at boot); inert file queued as a follow-up | logged |
| 339 | a `--verify` probe written `$(systemctl is-enabled "$u" \|\| echo absent)` corrupts its reading on the happy path | `systemctl is-enabled` exits 1 for a *disabled* unit, so the fallback fires when the answer is right and both writes are captured; a missing unit reports `not-found`, not empty | capture first, default only on empty; have the test execute the probe string, not just fake its output | SOLVED |
| 340 | hathor `gpu-lowpower.service` reports `active` but has applied nothing since 2026-09-19; both cards `auto`/`balanced` instead of `low`/`battery` | `ExecStart` globs `/sys/class/drm/card*/...`; glob matched nothing on the 09-19 boots, literal pattern passed through, every write failed; `\|\| true` + `exit 0` hide it from `is-active` | out of scope here; filed as a follow-up. Order the unit after the DRM nodes exist and fail loudly | logged |

## 6. Reversibility

Re-add the unit from `ops/wq2136-hathor-disable-gpus-unit/` (steps in its RUNBOOK). The blueprint
edits are additive — struck text is retained in place, so the original reads back intact.
