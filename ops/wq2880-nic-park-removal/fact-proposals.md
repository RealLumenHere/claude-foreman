# wq#2880 minerva fact proposals (for Frank review, never auto-applied)

Workers cannot write under `/home/lumen/minerva` (guard, wq#1719). These are for
`minerva/dream/convo-fact-candidates.md` or `lumen_state.py ingest` by whoever reviews them.

## CONTRADICTION: fleet.nic_policy (since 2026-08-07)

The claim "nic-park.service + /usr/local/sbin/nic-park removed on every node (verified
is-enabled=not-found on all 8)" is false for lumen. On 2026-09-28, lumen showed
`is-enabled=enabled`, the script was present, and `00:19.0` had been unbound since the
2026-08-31 boot (`reports/wq2757_lumen_i217_enp0s25_unbound_2026-09-28.md`). Other nodes
have not been re-audited yet. Run `ops/wq2880-nic-park-removal/audit.sh`.

## SUPERSEDE: lumen.nic_config, only after `apply.sh` exits 0

> ens1 = Realtek RTL8125 2.5GbE PCIe 01:00.0, MAC 3c:7c:3f:a6:be:dd, active primary,
> static 192.168.8.160/24 default via 192.168.8.1 metric 100. enp0s25 = onboard Intel
> I217-LM 00:19.0, MAC 64:00:6a:58:fc:d8 (NEVER SPOOF, Frank), bound to e1000e and
> enumerating, NO-CARRIER until cabled. If cabled, networkd dracut-default DHCP gives it
> metric 1024, so it is failover only. wait-online is pinned -i ens1. nic-park.service
> and /usr/local/sbin/nic-park are removed (wq#2880). Probe:
> ops/wq2880-nic-park-removal/verify.py
