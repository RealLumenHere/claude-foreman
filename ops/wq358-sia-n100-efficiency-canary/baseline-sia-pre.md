# Sia pre-install baseline (wq#358)

**Not independently captured by this session** — this wq358 fworker BUILD-lane profile
(`harness/worker-settings.json`) has no `ssh` allowlist entry at all, so `verify-sia.sh`
could not actually be run against Sia here. This is a stricter profile than the wq415/
Sobek session, which did have ssh and captured a live baseline
(`ops/wq415-sobek-n100-efficiency-canary/baseline-sobek-pre.md`). See
`reports/wq358_sia_n100_efficiency_canary.md` and `kb/ISSUE-LOG.md` #39 for the full
constraint history (four prior wq#358 attempts hit the same wall).

The values below are transcribed from the wq#358 ticket text itself (recorded there as
"captured 2026-07-17", i.e. sourced from whoever ran the original fleet survey for
wq#357/358, not measured by this session):

```
PL1 = 10W (kernel-side, already at target — unlike Bes where kernel showed 20W
      overriding a 12W BIOS setting; Sia needs no PL1 change)
PL2 = 25W (target is 15W per the wq#357 pack spec — this DOES need to change on Sia)
cpufreq governor = intel_pstate / powersave
EPP = balance_performance (target: balance_power)
swappiness = 60 (target: 10)
NIC = RTL8125 2.5G PCIe, interface enp1s0, LAN static .174
PG17, miniflux, news_intake = containerized (docker), not systemd
```

## Before running deploy-sia.sh

1. Run `verify-sia.sh` first (from a session with ssh+root reach) to capture the *actual*
   current-state snapshot and overwrite/supplement this file — the values above are
   ticket-recorded, not fresh reads, and it's been over a month since they were noted.
2. Confirm the wq#357 Bes soak (48h+, PG standby lag=0, HA/mosquitto stable, no NIC
   drops) is documented clean somewhere before applying to Sia — this repo has no
   soak-close report for Bes as of this staging.
3. Sia is the ASPM=powersave canary (ticket: "ASPM=powersupersave has broken this NIC
   class before"). Confirm the pack's ASPM setting for Sia matches what the ticket
   intends to test (`powersave`, not `powersupersave`) before install.
