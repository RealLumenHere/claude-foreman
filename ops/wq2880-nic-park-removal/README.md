# wq#2880: finish the 08-07 nic-park reversal on Lumen

Root cause is in `reports/wq2757_lumen_i217_enp0s25_unbound_2026-09-28.md`.
`nic-park.service` from the 2026-08-03 experiment was never removed on Lumen. It unbinds
the onboard I217-LM (`0000:00:19.0`, `enp0s25`) from e1000e at every boot.

## Files

- `verify.py` is the acceptance probe. It is read-only and needs no root. It exits 0 only
  when all four hold: `00:19.0` is bound to e1000e, its netdev is `enp0s25` at
  `64:00:6a:58:fc:d8` (never spoof), `systemctl is-enabled nic-park.service` prints
  `not-found`, and ens1 keeps the lowest default route at metric 100.
- `test_verify.py` holds fixture tests for the fixed state, the parked state, a spoofed
  MAC, a renamed netdev, and route-metric regressions.
- `apply.sh` is the root fix for Lumen only. It refuses unless `00:19.0` is `8086:153a`
  and ens1 exists. It backs up the unit and script to
  `/home/lumen/maint/wq2880-nic-park-backup/`, disables the unit, removes both files, runs
  daemon-reload, and binds only `0000:00:19.0` to e1000e. It does not use
  `nic-park --undo`. It finishes by running `verify.py`. It is idempotent. It needs no
  reboot and does not touch ens1.
- `audit.sh [node ...]` is a read-only fleet check. It prints one line per node:
  `unit=<is-enabled> script=<present|absent> unbound_nics=<pci addrs>`. With no
  arguments it covers every node that has a `tailscale_ip` fact. `UNREACHABLE` means ssh
  failed. It does not mean the node is clean.

## Run it (operator with root on Lumen)

```
ssh lumen@100.117.66.102 'sudo /home/lumen/foreman/ops/wq2880-nic-park-removal/apply.sh'
ssh lumen@100.117.66.102 '/home/lumen/foreman/ops/wq2880-nic-park-removal/audit.sh'
```

Any node where `audit.sh` reports `unit=enabled` or `script=present` needs the same
treatment. Disable the unit, remove the unit file and `/usr/local/sbin/nic-park`, then
bind each listed PCI address to its own driver.

## Why this lane did not run it

The build-worker lane has no root path. The guard denies the privilege-escalation verb
and `systemctl disable`. `/etc` and `/usr/local/sbin` are outside the sandbox, `ssh` is
not allow-listed, and `fleet-exec run` denies with `grant:grant_missing_env`. This is
ISSUE-LOG #192 and the wq#2757 report again.

Baseline on 2026-09-28, from live `verify.py` on Lumen, exit 1:

```
FAIL driver: UNBOUND
FAIL netdev: none
FAIL nic-park.service: enabled
PASS default routes: [('ens1', 100)]
```

## Minerva follow-ups

These are proposals only, in `fact-proposals.md`. Workers cannot write under `minerva/`.

- `fleet.nic_policy`: contradiction note. Lumen falsifies the "removed on every node
  (all 8)" claim.
- `lumen.nic_config`: supersede it once `apply.sh` exits 0.
