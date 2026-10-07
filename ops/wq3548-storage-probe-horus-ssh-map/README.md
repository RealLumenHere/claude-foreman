# wq#3548 — horus has no SSH mapping (inventory lookup flaky, legacy map missing)

Raised by wq#3537's "Not this ticket" note: `storage_probe.py --run` at
2026-10-07T07:25Z logged `[horus] transient: first collect failed (no ssh
mapping (inventory + legacy)), re-probe after 15s OK`. horus was added to the
manifest at 04:43Z (wq#3497/wq#3537) but `manifest_add_node`'s step 2 ("add the
new node to the probe SSH map") was never done.

## Root cause

`storage_probe.py`'s `ssh_target(node)` resolves from the fleet inventory
first and only falls back to the hardcoded `SSH_FALLBACK` dict ("legacy map")
when the inventory lookup fails for that one call — by design, so a transient
inventory/state-store hiccup doesn't take a node's probe down. horus's
inventory record (`/home/lumen/fleet/inventory/machines/horus.yaml`) is
complete and correct (`network.ssh.user: horus`, `network.tailscale_ip:
100.83.19.124`) and resolves cleanly on every repeat read tested (5/5
consecutive `fleet_inventory.ssh_target("horus")` calls, see Evidence) — the
inventory itself is not flaky. But `SSH_FALLBACK` had no `horus` entry, so the
one time the inventory path *did* hiccup (coinciding with wq#3537's
`apply_overlay.py --apply` writing `storage_manifest.yaml` around
07:24-07:51Z, the same window as the logged failure), the fallback had nothing
to serve and `ssh_target()` returned `None` -> `"no ssh mapping (inventory +
legacy)"`. Every other fleet node has a `SSH_FALLBACK` entry; horus was simply
never added to it because `manifest_add_node` step 2 is a manual step the
2026-10-07 04:43Z add skipped.

## Fix (live, `/home/lumen/agents/storage-audit`, commit `3bfcae9`)

`storage_probe.diff` here is that commit: adds
`"horus":"horus@100.83.19.124"` to `SSH_FALLBACK` in `storage_probe.py`
(implementing line: `storage_probe.diff:26`). This is the manifest_add_node
step 2 that was skipped — horus now has a stable fallback mapping, so a future
inventory blip on this node degrades the same way every other node's does
(silent fallback) instead of surfacing as a probe DEFECT.

## Evidence

- `test_add_node_overlay.py` + `test_parked_set.py` + `test_plug_autoclear.py`:
  23/23 pass after the change (no behavior touched besides adding a dict key).
- 5 consecutive `fleet_inventory.load()`/`ssh_target("horus")` calls, 1s apart:
  all resolved `horus@100.83.19.124` from the inventory, 0 exceptions — the
  inventory resolution path is healthy now; the wq#3537-era blip was a
  one-time race against concurrent manifest writes, not a recurring defect.
- `storage_probe.py --run` post-fix: no horus DEFECT/unreach line at all
  (`PASS storage-integrity: 11/16 nodes ... unreach: farida,gameboy,apep,seshat,thor`
  — horus is neither unreachable nor logging a legacy-map DEFECT).

## Not this ticket

- The other `unreach`/DEFECT nodes in the post-fix run (farida, apis, gameboy,
  apep, seshat, thor) are pre-existing, separately tracked conditions
  (farida/apis already carry their own legacy-map DEFECT lines predating this
  ticket; apis is `n/a` by design / parked).
