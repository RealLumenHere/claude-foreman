# Sobek pre-install baseline (wq#415)

Captured 2026-08-22T07:08Z via `verify-sobek.sh`, before any n100-efficiency (wq#357)
file has touched the host. Host: Sobek, Ubuntu 26.04 LTS, N100/4C4T, TS 100.86.156.91.

```
PL1_uW=20000000
PL2_uW=25000000
EPP=balance_performance
swappiness=60
unit_active=inactive
unit_enabled=not-found
pci_sata_00:17.0=on
pci_nic_01:00.0=on
pci_nvme_02:00.0=on
thermald_active=active
pg_active=active
pg_isready=/var/run/postgresql:5432 - accepting connections
walreceiver=4
postgres: 18/main: walreceiver streaming 0/1501B5D8
pg_journal_errors_last200=0
```

## Notes
- RAPL PL1/PL2 confirm the same kernel-overrides-BIOS behavior documented for Bes in
  wq#357: kernel default here is 20W/25W, not the board's BIOS PL1.
- `intel-rapl:0` constraint names are `long_term`/`short_term` as n100-efficiency.sh
  expects — no adaptation needed for this board.
- i915 is loaded on Sobek (`lsmod | grep i915` non-empty), unlike the "both nodes
  headless, unlikely" assumption in the pack README written for Bes/Sia. The i915
  modprobe.d options still only take effect on next boot; install does not reboot.
- Hardware differs slightly from the FLEET_MAP note (RTL8125 2.5G): Sobek's NIC is an
  onboard RTL8111/8168 gigabit part (`lspci -nn`: `10ec:8168`), class `0200`, still
  covered by the pack's `0x02*` PCI exclusion.
- `/sys/class/powercap/intel-rapl:0/energy_uj` is root-only on this board (permission
  denied as the `sobek` user) — no non-privileged before/after watt estimate is
  possible via RAPL; Sobek also has no metered smart-plug (FLEET_MAP: "POWER:
  UNMETERED"). Soak verification here is stability-only (replication health, unit
  state), not a watt delta, unless wq#345's hw-inventory exporter already covers it.
- Vaultwarden promote payload on Sobek is parked (`restart=no`) and not checked here:
  `docker ps` needs a group the `sobek` user isn't in (`permission denied ... docker
  API`), and the pack doesn't touch containers. Out of scope for this verify.
