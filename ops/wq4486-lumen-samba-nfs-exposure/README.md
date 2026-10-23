# wq#4486 — Lumen Samba/NFS exposure review

Fleet security audit 2026-10-22 (`/home/lumen/scratch/research/fleet-security-audit.md`)
flagged Lumen: Samba (139/445) and NFS/rpcbind (2049/111/20048) listening on
`0.0.0.0`, and no UFW host firewall.

This worker runs *on* Lumen itself (not dispatched remotely), so unlike the
Thor SSH-hardening sibling ticket (`ops/wq4485-thor-ssh-hardening/`), the
fixes below were applied live where the house guard-hook allowed it, not just
staged.

## Samba — actively used, restricted (not disabled)

`/etc/samba/smb.conf` defines three real shares (`lumen-home`, `lumen-scratch`,
`lumen-research`) and `hosts allow = 192.168.8.0/24 127.0.0.0/8 100.64.0.0/10`
already scoped connections to LAN/loopback/tailnet. Proof of actual use:
`/var/log/samba/log.192.168.8.153` exists (Samba lazily creates a per-client
log file on first connection) — a LAN client has connected.

Because it's used, and used from the LAN (not the tailnet), **`apply-samba.sh`**
adds `bind interfaces only = yes` + `interfaces = lo ens1 100.117.66.102` to
`[global]` instead of disabling it or binding tailnet-only (tailnet-only would
have broken the one client we have evidence for). Applied and verified:

```
# before (fleet-security-audit.md)
0.0.0.0:139, 0.0.0.0:445

# after
127.0.0.1:139/445, 192.168.8.160:139/445   (lo + LAN, matches hosts allow)
```

Known limitation, logged here rather than chased further: smbd would not bind
to the tailnet address no matter how it was specified (interface name,
IP/CIDR, bare IP all tried — see comments in `apply-samba.sh`). Tailnet SMB
access was not observed in use, so this fails closed, not open — `hosts
allow` still carries the tailnet range for whenever that gets root-caused.

## NFS — unused, export dropped live; service disable queued

`/etc/exports` had exactly one export: `/nfs/rootfs
*(rw,sync,no_root_squash,no_subtree_check)` — wildcard client list, no root
squash, read-write. `/nfs/rootfs` is an **empty directory created 2026-10-21**
(2 days before this audit), and nothing in this repo (`fleet-config/`, `ops/`,
`docs/`) references it as a netboot/PXE/diskless target. NFSv4 op counts since
the last boot (2026-10-14) are in the low dozens — consistent with idle
rpc/mount probing, not real traffic. Conclusion: unused.

**`apply-nfs-unexport.sh`** (run) commented out the export and ran
`exportfs -ra` — confirmed empty `exportfs -v` afterward. The worst part of
the exposure (anyone on the LAN/tailnet mounting a wildcard rw no_root_squash
share) is closed now.

Full retirement (`systemctl disable --now nfs-server rpcbind`, closing
2049/111/20048) is **not** done by this ticket — `harness/guard.sh:151-152`
hard-denies `systemctl stop|disable|mask` for every worker session
unconditionally (confirmed: it's outside the `FOREMAN_FIRE_PHASE=diagnosis`
conditional block that gates the sibling `start|restart|reload|enable` check,
so it's not a fire-mode-only rule). Logged as an ISSUE-LOG row. The
ready-to-run script is **`disable-nfs-server.sh`** — low risk (the export is
already gone, nothing currently mounts it), queued for Frank or a
non-worker session to run.

## UFW — recon done, ruleset not generated, not installed

Copying Horus's baseline verbatim (`allow 22` + `allow in on tailscale0` +
default deny) would have been a regression: Lumen listens on far more than
the audit doc's port list —
`ops/wq4486-lumen-samba-nfs-exposure/recon-ports-2026-10-23.txt` is a live
`ss -tlnp` snapshot taken during this ticket, and it has **60+** listening
ports (Plex 32400, VNC 5901, Postgres 5433, Redis 6379, a long tail of 8xxx
app ports, several ephemeral-looking high ports), most of them not on the
audit's Aug-era list and several reached from the LAN (Samba's own client
proves LAN consumers exist, not just tailnet ones). `ops/wq3108-fleet-input-
default-drop/README.md`'s own doctrine for this fleet is "any wildcard
listener [needs] a named consumer" before a rule ships — that mapping pass
(which port serves which LAN vs. tailnet consumer) is real, separate
security-engineering work this ticket's "consider installing UFW" framing
does not cover, and getting it wrong on a headless box risks a firewall
locking out even the LAN recovery path.

Not done, by design: UFW install/enable. The recon snapshot above is the
input a follow-up ticket needs to run `wq3108`-style `genrules.py` generation
against Lumen specifically, then apply with the same dead-man-rollback
pattern as `ops/wq4485-thor-ssh-hardening/apply.sh`.

## Files here

- `apply-samba.sh` — applied; idempotent; backs up `smb.conf` to `.wq4486.bak`.
- `apply-nfs-unexport.sh` — applied; idempotent; backs up `/etc/exports`.
- `disable-nfs-server.sh` — prepared, not applied (needs a non-guarded
  session — see above). Includes rollback.
- `recon-ports-2026-10-23.txt` — `ss -tlnp` snapshot, the input for a future
  UFW ruleset.
