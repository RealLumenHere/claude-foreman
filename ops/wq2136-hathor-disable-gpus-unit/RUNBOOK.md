> **RETIRED — do not install (wq#2157, 2026-09-20).** The RCA this bundle was waiting on has landed
> (`research/ISIS_POWEROFF_RCA.md` §5, PR #708) and the verdict for this unit is **retire, not install**.
> The unit is measurably harmful: it costs ~16.6 W at idle, its unbind step fails on every start, and it
> reintroduces the pattern the 2026-06-25 CRITICAL REVERSAL forbids. The `## Apply` section below is kept
> only as the reversal path. Nothing here should be run without a new decision and a wall-power number
> that beats 86.6 W idle.
>
> Note on the earlier banner: this replaces a HOLD banner whose reasoning cited the GPU-disable-causes-
> poweroffs hypothesis. That hypothesis was **falsified** (wq#2140 §§1-4) — isis's power losses are not
> caused by this unit. The unit is retired on its own merits, not as a crash fix.

# wq#2136 — hathor disable-gpus.service unbalanced quoting (superseded by wq#2157 retirement)

## Why it is retired (wq#2140 RCA §5)

1. **It costs power.** Idle wall, identical MacPro6,1 hardware (`plugs.json`, 2026-09-20T15:56:51Z):
   isis with both cards PCI-removed and unmanaged **103.2 W**, against hathor with both cards
   amdgpu-bound **86.6 W** — **+16.6 W**, against a June baseline of ~78-80 W for this chassis.
2. **Half of it has never worked.** `/sys/bus/pci/drivers/amdgpu/unbind` returns `Permission denied` on
   every start, so only the PCI `remove` lands. The `gpu-lowpower` knob lives in amdgpu sysfs and
   disappears with the device, so `dpm=low` is stripped with nothing replacing it.
3. **It violates the standing rule.** `claude_memory.md:4053`, *CRITICAL REVERSAL 2026-06-25*: leave both
   GPUs amdgpu-bound with `dpm=low` on isis/hathor/nephthys/khepri. This unit is the third attempt at the
   same idea (gate → vfio-override → PCI-remove) and the third to measure worse than leaving it alone.

The Mac blueprint (`/home/lumen/MAC_POWER_DEPLOYMENT.md`) was corrected under wq#2157 so that PHASE 2 no
longer reads as live advice to disable the GPUs on these nodes.

## Live state on hathor and nephthys (verified 2026-09-20, wq#2157)

| Check | hathor | nephthys |
|---|---|---|
| `systemctl is-enabled disable-gpus.service` | `disabled` | `disabled` |
| `systemctl is-active disable-gpus.service` | `inactive` | `inactive` |
| in `multi-user.target.wants/` | no | no |
| `ls /sys/bus/pci/drivers/amdgpu/` | `0000:02:00.0`, `0000:06:00.0` | `0000:02:00.0`, `0000:06:00.0` |
| unit file on disk | present, 376 B, Sep 19 09:09 | present, 376 B, Sep 19 09:09 |

The unit is functionally retired on both nodes: disabled, inactive, not wanted by any target, and both
D300s are amdgpu-bound as the standing rule requires. It cannot start at boot in this state.

**Residual:** the unit *file* is still on disk on both nodes. Deleting it needs a mutating remote verb;
`fleet-exec run` is read-only (`harness/fleet-exec-policy.yaml:180` `run_readonly_cmds`) and the `sudo`
verb is still v2/unimplemented (same file, line 207). Tracked as a follow-up; the file is inert while the
unit is disabled and unwanted.

## Original finding (read-only, 2026-09-20, wq#2136)

- hathor `/etc/systemd/system/disable-gpus.service:7`: the `for p in 0000:02:00.0` / `0000:06:00.0; do ...` list is split across two lines inside the single-quoted `bash -c` argument. systemd reports `Unbalanced quoting`, the unit loads as `bad-setting`, and it has never run.
- isis had the same unit with the loop on one line (correct) — which is why isis, and only isis, ever ran it.
- nephthys has the same broken two-line form as hathor.

`disable-gpus.service` in this directory is the corrected one-line form. It is retained as the reversal
artifact for the RCA's "Reversible: re-add the unit", **not** as something to deploy.

## Verify

    bash install.sh --verify

Read-only. Asserts the retired state above on each node (unit not enabled, not active, not wanted, both
D300s amdgpu-bound). Exit 0/1/2 per `ops/VERIFY_CONTRACT.md`. The non-verify path installs nothing.

## Reversal (only with a new decision)

Per node, as root:

    install -m 0644 disable-gpus.service /etc/systemd/system/disable-gpus.service
    systemctl daemon-reload
    systemctl enable --now disable-gpus.service

`systemctl start` hot-removes the GPUs from the PCI bus with no reboot. Re-retire by reversing:
`systemctl disable --now disable-gpus.service`, then reboot so amdgpu rebinds both cards, then confirm
`gpu-lowpower.service` has restored `dpm=low` on every card.
