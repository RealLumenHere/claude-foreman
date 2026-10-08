# wq#3650: Mac Pro WoL integration — wake-on-demand dispatch + auto-sleep

Scope: isis and hathor (Apple MacPro6,1, E5-2697 v2 12c/24t, 64 GB; two Curacao XT GPUs each —
the ticket said D700, the fleet's own recon corrects that to **D300**-class,
`research/wq2076_macpro_gpu_recon.md`, confirmed again in `research/INVENTORY_RECONCILIATION.md`
lines 116/126). khepri and nephthys are the same chassis family but are out of scope here: khepri
is the designated bench box, nephthys has been offline since before this ticket was filed.

## What this ticket's 7 items map to

| # | Ask | Status |
|---|-----|--------|
| 1 | Enable WoL via ethtool systemd service | **New in this bundle**: `macpro-wol-enable.service` |
| 2 | Force the GPUs to lowest power state | **Already live**, not re-shipped here — `ops/wq2166-gpu-lowpower-ordering` (dpm=low, battery) has run on both nodes since wq#2166/wq#2178 |
| 3 | Install mbpfan (low=50 high=75 max=85) | **Already live**, not re-shipped here — `ops/mbpfan` (wq#2508), PASS on both isis and hathor. The deployed `ops/mbpfan/mbpfan.conf:6-12` carries `low_temp=50 high_temp=70 max_temp=80` — low matches this ticket's ask, high/max are 5°C lower, chosen from wq#2508's own soak evidence (`research/MBPFAN_MACPRO61.md` §8), not this ticket's unverified 75/85. Left as-is rather than overwritten blind; re-tuning to 75/85 would need its own soak first |
| 4 | Limit Ollama to 12 physical threads | **New in this bundle**: `10-wq3650-threads.conf` |
| 5 | Disable turbo boost | **New in this bundle**: `macpro-no-turbo.service` |
| 6 | Add wake_judge.sh to the dispatch pipeline | **New**: `dispatcher/wake_judge.sh` + `dispatcher/wake_judge.py`, and `dispatcher/node_power.py`'s own `wake()`/`power_down()` now branch on `wake.method: wol` (dispatcher/tests/test_node_power.py, dispatcher/tests/test_wake_judge.py) |
| 7 | Auto-sleep cron (load < 0.2 for 30 min) | **New in this bundle**: `macpro-autosleep.timer` + `.sh` |

Items 2 and 3 were already shipped by earlier tickets against the same two nodes and are not
duplicated here — re-running them would just be noise in `ops-deploy-drift`. See their own
`README.md`s / research docs for the values that actually landed: GPU power state is
`power_dpm_force_performance_level=low` + `power_dpm_state=battery` (not a raw `D700`-style
knob — these are D300-class cards), and mbpfan's thresholds are the §8-soaked 50/70/80, not this
ticket's unverified 50/75/85.

## The Apple EFI WoL quirk, and why it shaped three decisions here

Frank's ticket: "Apple EFI WoL quirk: loses WoL after power loss until clean shutdown." On a
MacPro6,1 the Wake-on-LAN flag that lets a magic packet bring the box out of S5 lives partly in
EFI/NVRAM state, not just the Linux NIC driver register `ethtool` sets. A **hard power loss** (AC
cut, not an OS-initiated shutdown) can clear that EFI-side flag; the fix is one full clean boot
followed by an OS-initiated `systemctl poweroff` — never another AC cut — before the next magic
packet is trusted to land. Concretely, that means:

1. `macpro-wol-enable.sh` re-arms `ethtool -s enp12s0 wol g` on **every boot** regardless —
   that register does not survive a reboot on its own, independent of the EFI quirk.
2. `macpro-autosleep.sh` always sleeps via `systemctl poweroff` (clean), never a plug/relay cut.
3. `dispatcher/node_power.py`'s drain sequence (§6.3) already issued a clean `systemctl poweroff`
   for every cold node before this ticket (step 4) — what changed (wq#3650) is step 6: for a node
   whose `wake.method` is `wol`, the plug-off is now **skipped entirely**. Mains power has to stay
   on for the NIC's standby rail to answer a magic packet at all; a smart-plug AC cut would read
   as "idled down" in the registry but actually defeat every future wake. See
   `dispatcher/node_power.py:456-470` (the `method != "wol"` guard around the old unconditional
   plug-off) and its test `test_wol_node_drain_skips_plug_off_and_leaves_ac_on`.

Nothing here has been run against a real hard-power-loss recovery (that needs hands on the box —
queued as a physical follow-up, not a gate on landing this bundle, per instinct 3). The dispatcher
code path is exercised by unit tests only; verifying the actual EFI behavior on isis/hathor is the
one part of this ticket that cannot be done from a sandboxed build worktree.

## MACs (verified, not guessed)

Both nodes' fleet-LAN NIC is `enp12s0` (the other onboard port, `enp11s0`, is unused on both —
`research/INVENTORY_RECONCILIATION.md:117,127`, live-probed 2026-09-10):

| node | iface | MAC |
|------|-------|-----|
| isis | enp12s0 | `00:3e:e1:c8:27:97` |
| hathor | enp12s0 | `00:3e:e1:c9:07:48` |

These are now also in `research/inference_nodes.yaml` under each node's `wake.mac` (schema:
`research/INFERENCE_NODE_REGISTRY.md` §2). Re-verify with `ip link show enp12s0` on the box
before relying on them for a real wake — a NIC swap would silently stale this table.

## Why isis/hathor still wake via plug-ac today, not WoL

`wake.method` for both nodes is left as `plug-ac` in this PR — the dispatcher code added here
(`node_power.py` branching on `method: wol`, `wake_judge.py`) is real and tested, but **not
switched on** for either node yet. Three reasons, all about blast radius on two nodes a human
explicitly decided the power policy for:

- hathor is `power_policy: always-on`, by Frank's own 2026-09-20/09-26 decision (dedicated to the
  judge) — this ticket does not touch that.
- isis is `power_policy: warm`, `state: down` since wq#2140's unresolved abrupt-power-loss RCA
  (`research/ISIS_POWEROFF_RCA.md` §6) — flipping it to `cold` + `wol` now would be the first time
  anything auto-power-cycles a node whose last three power losses are still unexplained hardware
  suspects. That decision needs the §6.3 P5 physical AC test first, not a side effect of this
  ticket.
- Flipping either node to `power_policy: cold` also needs a real `measured_ready_s` from
  `fleet-node bench` (currently `null` for both, meaning "never auto-woken" regardless of method
  — `research/INFERENCE_NODE_REGISTRY.md:346`), which this build worker has no grant to run.

Follow-up ticket, once P5 clears and a bench measurement exists: flip `isis.wake.method` to `wol`,
set `power_policy: cold`, and soak it the way `ops/mbpfan` was soaked (wq#2508 §8) before trusting
it unattended.

## Why both auto-sleep mechanisms exist

`macpro-autosleep.sh` (this bundle, host-local, `load1 < 0.2` for 30 min) and
`dispatcher/node_power.py`'s idle-drain (fleet-level, in-flight-unit-aware, only acts on
`power_policy: cold` nodes) are deliberately two different mechanisms, not a duplicate — see
`macpro-autosleep.sh`'s header comment. Today only the first one can ever fire on isis/hathor,
because neither is `cold`. If a future ticket flips one to `cold`, re-examine whether this
cron is still needed alongside the dispatcher's own drain, or whether it should be disabled to
avoid two independent idle-power deciders racing each other.

## Deploy

No lane available to this build worker has `ssh`+root to isis or hathor (`harness/worker-settings.json`
carries no `ssh` grant here, same blocker `research/MBPFAN_MACPRO61.md` §5 hit for wq#2508's first
attempt). This bundle ships the artifacts; apply them from a lane that does:

```sh
scp -r ops/wq3650-macpro-wol lumen@<isis-or-hathor-ip>:/tmp/
ssh lumen@<isis-or-hathor-ip> 'sudo bash /tmp/wq3650-macpro-wol/install.sh'
```

Verify from Lumen afterward (read-only, no grant needed beyond ssh):

```sh
bash ops/wq3650-macpro-wol/install.sh --verify
```
