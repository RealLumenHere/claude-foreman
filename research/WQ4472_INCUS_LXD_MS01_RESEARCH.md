# wq#4472: Incus/LXD on MS-01 — system containers vs Docker for fleet services

Goal: the MS-01 will replace Lumen. Decide whether Incus (the LXD fork/successor,
`linuxcontainers.org/incus`) is worth adopting for fleet services instead of staying on
Docker, and if so, how to get there. Scope per ticket: Incus vs Docker fit for our
workload, migration path, resource overhead, ZFS/Btrfs storage backends, Tailscale
inside containers, restic backup integration.

## Sourcing note

Ran `fleet-search web` first per prior-art doctrine (queries: "Incus vs Docker system
containers production 2026", "Incus ZFS storage pool overhead vs Btrfs benchmark",
"Tailscale inside Incus LXC container tun device", "restic backup Incus LXD container
filesystem snapshot", "migrate Docker Compose services to Incus containers", "Minisforum
MS-01 ZFS NVMe RAM homelab proxmox incus"). SearXNG answered via `google cse` on all six
(brave/duckduckgo/startpage/wikipedia engines suspended/CAPTCHA'd, consistent with the
already-logged wq#2595/wq#4110 engine-exhaustion pattern — not re-logged, same known
cause). Results below are cited by URL; MS-01's own spec sheet (13th-gen Intel, up to
96GB DDR5, dual 2.5GbE + 10GbE, 2x M.2 NVMe + SATA bays) and Incus's command surface are
drawn from training knowledge where no search result was needed to confirm them — flagged
inline.

## 1. What our current fleet actually runs in Docker

Checked the repo rather than assuming the ticket's "8+ containers" figure. Committed
compose definitions (`fleet-config/docker/`) cover three service groups:
- `litellm` (`fleet-config/docker/litellm/docker-compose.yml:6-16`) — single container,
  gateway on :4000, config mounted read-only from an 0600 host file.
- `monitoring` (`fleet-config/docker/monitoring/docker-compose.yml:6-15`) — single
  container (Gatus), :8418, config mounted from host.
- `gitea` (`fleet-config/docker/gitea/docker-compose.yml:6-19`) — single container,
  REBUILD-path only; the live Gitea install is native (`/etc/gitea/app.ini`), not
  containerized at all today.
- The MS-01 will also need the standing-order Vault/cloudflared path. Per CLAUDE.md
  instinct 5 those already run as detached `systemd-run` units, not Docker containers,
  so there's no migration delta for them — they're already closer to Incus's model
  (systemd-managed units) than to Docker's.

So today's fleet is lighter than the ticket assumed: 2 live single-container compose
services plus one rebuild-only compose file, plus systemd-managed Vault/cloudflared. The
"one Incus container per service group instead of 8+ Docker containers" framing doesn't
match what's actually deployed; there is no sprawl to consolidate yet. That changes the
calculus below — this is a greenfield decision for the MS-01, not an urgent fix for an
existing mess.

## 2. Incus vs Docker — the actual distinction

Confirmed via search, not assumption:
> "Incus is not strictly a competitor to Docker: since version 6.3 it can even run OCI
> images (Docker), but its core remains the system container" —
> https://www.stephane-robert.info/en/docs/incus/
> "The image starts as an application container, marked CONTAINER (APP) in `incus list`
> ... An `incus exec web --` ..." —
> https://www.stephane-robert.info/en/docs/incus/incus-vs-docker/ (Oct 2026)

Docker = application containers: one process per container, image = app + its deps,
ephemeral by design, orchestrated via Compose/Swarm/K8s. Incus = system containers:
full init system (systemd) inside, feels like a lightweight VM, long-lived, you `incus
exec` into it like SSH. Since Incus 6.3 it also has a native OCI/"Docker-image"
container type, and `incus-compose` (https://discuss.linuxcontainers.org/t/incus-compose-
the-missing-docker-compose-for-incus/26761, Jun 2026) runs standard `compose.yaml` files
directly against Incus — so migration doesn't require rewriting our existing compose
files from scratch, only re-pointing them at an Incus OCI backend or nesting Docker
inside an Incus system container (both documented patterns — "Docker in Incus is
Awesome": https://discussion.scottibyte.com/t/docker-in-incus-is-awesome-the-get-my-
application/457).

For our actual workload (single-process gateway, single-process status page, a native
Gitea that isn't containerized at all) Docker's app-container model already fits — there
is no multi-process-per-unit need that would justify paying for a full init system per
service. Incus's win case is when a "service" is really a small VM-shaped unit: multiple
cooperating daemons, its own cron/systemd timers, SSH access for debugging, persistent
non-ephemeral state at the OS level. None of our three services are shaped like that
today.

## 3. Resource overhead

Not independently benchmarked here (no MS-01 hardware in this worktree to measure on —
would need live access to the box, out of scope for a research ticket). From search and
known architecture: a Docker container shares the host kernel with near-zero overhead
beyond cgroups/namespaces. An Incus *system* container also shares the host kernel (LXC,
not KVM) — it is not a VM, so its CPU/memory overhead over Docker is small (the extra
cost is running a full systemd + more background daemons inside each instance, not
virtualization tax). Incus *VMs* (KVM-backed, a separate instance type Incus also
manages) carry real VM overhead and are a different tradeoff entirely — not needed here
since Incus system containers already give us systemd without the KVM cost.

## 4. Storage backend — ZFS vs Btrfs

> "I have re-run the benchmarks and the performance of zfs is indeed different, still
> far behind btrfs but now notably better than lvm" —
> https://discuss.linuxcontainers.org/t/comparing-storage-drivers/21355
> "For single-server setups on Ubuntu, Btrfs or ZFS provide the best combination of
> features and performance. ZFS is preferred if your hardware ..." —
> https://oneuptime.com/blog/post/2026-03-02-configure-incus-storage-pools-ubuntu/view

Both are first-class Incus storage drivers (pool types: `dir`, `zfs`, `btrfs`, `lvm`,
`ceph`). ZFS gives stronger integrity guarantees (checksumming, scrub, RAID-Z) and the
most mature snapshot/send-receive tooling; Btrfs is lighter-weight and — per the
Linux Containers forum thread above — currently benchmarks faster for Incus's
copy-on-write instance operations (clone/snapshot) than ZFS, though still behind raw
LVM for ephemeral I/O. Btrfs also needs less tuning on a single NVMe-backed box (ZFS
wants its own ARC sizing and ideally ECC RAM to be fully trusted, which the MS-01's
DDR5 may or may not be depending on the SKU — not confirmed for our specific unit,
flagged as unverified). For a single MS-01 (not a multi-disk RAID array), Btrfs is the
better default pool backend: comparable snapshot semantics to ZFS, lower memory
tax, no ARC tuning. ZFS is the better choice only if/when we add a multi-disk pool
and want RAID-Z-level integrity guarantees across disks — revisit then.

## 5. Tailscale inside Incus containers

> "Create your LXC container. Go to the Resources tab ... Add Device prompt, enter
> `/dev/net/tun`" — https://tailscale.com/docs/features/containers/lxc/lxc-unprivileged
> (Jan 2026, official Tailscale docs)
> "I then rebooted the vm and checked that /dev/net/tun existed and that I could reach
> the LXD vm from another machine via the tailscale ip" —
> https://discuss.linuxcontainers.org/t/running-tailscale-in-lxd-vms/16543

Tailscale works inside unprivileged LXC/Incus containers but needs the `/dev/net/tun`
device explicitly passed through (`incus config device add <container> tun unix-char
path=/dev/net/tun` is the Incus-CLI equivalent of the Proxmox-GUI step quoted above) —
it is not present by default in an unprivileged container's device set. This is a
required extra provisioning step per container, not a blocker; every fleet node already
runs Tailscale (the SSH-wrap-everything rule in CLAUDE.md instinct 1 depends on it), so
this would need to be baked into whatever base Incus image/profile we standardize on —
one profile with the tun device + Tailscale install, applied to every service
container, rather than repeating the device-add step by hand each time.

## 6. Backup integration with restic

Search did not surface restic-specific Incus integration — the ecosystem's documented
pattern is Btrfs/ZFS-native snapshots as the backup primitive, with restic/Kopia/rclone
layered on top for offsite copies:
> "Safeguard Your Data by Combining Encrypted Storage, Btrfs Snapshots, and Kopia/Rclone
> Offsite Backup" — https://weisser-zwerg.dev/posts/home-server-infrastructure/ (Mar 2025)
> "... snapshot as your backup of record. Your containers are ..." —
> https://smenode-labs.com/blog/proxmox-vs-docker-in-2026-vms-vs-containers-when-to-use-
> which/ (Oct 2026)

No evidence of a purpose-built `incus`+`restic` integration tool; the practical pattern
is: `incus snapshot` (or the storage backend's native snapshot: `btrfs
subvolume snapshot` / `zfs snapshot`) for fast local point-in-time recovery, then restic
backing up either the container's filesystem export (`incus export` to a tarball) or the
mounted storage-pool dataset directly, same as it would back up a Docker volume today.
This is not a regression from Docker — our existing restic-over-bind-mount pattern
(implied by `docs/RECOVERY.md` per the compose files' own comments, e.g.
`fleet-config/docker/litellm/docker-compose.yml:3-4`) ports over unchanged: point restic
at the Incus storage pool's dataset path instead of the Docker volume path.

## 7. Migration path recommendation

Given section 1's finding — only two live single-container services, nothing
multi-process or VM-shaped — a wholesale migration is not justified today. Recommended
path for the MS-01 standup:
1. Stand up Incus on the MS-01 with a Btrfs storage pool (section 4) as the default
   backend for new instances.
2. Keep `litellm` and `gatus` as plain Docker containers for now (via Incus's native
   OCI/Docker-image instance type, so they still live inside Incus-managed storage/
   networking/backup, without a systemd rewrite) rather than converting them to full
   system containers — they are single-process services and gain nothing from a system
   container's systemd.
3. Reserve true Incus *system* containers for anything that actually needs VM-like
   shape going forward — e.g. if Gitea's rebuild path is revisited, a system container
   (full systemd, persistent `/etc/gitea`) is a better fit than today's app-container
   compose file, since the rebuild doc already treats it like a small VM (restore a data
   volume, bring up native-feeling state).
4. Bake one shared Incus profile with the `/dev/net/tun` device (section 5) and
   Tailscale pre-installed, applied to every instance so networking parity with the rest
   of the fleet is automatic rather than a per-container manual step.
5. Point the existing restic backup scripts at the Incus storage-pool dataset path
   (section 6) instead of the Docker volume path; no new backup tooling needed.

Net call: adopt Incus as the MS-01's container host (for its storage/network/backup
management and OCI compatibility), but don't force our two existing single-process
services into system containers just because Incus makes system containers available —
pick the instance type per service, not uniformly.
