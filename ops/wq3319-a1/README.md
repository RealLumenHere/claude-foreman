# wq#3319 — [physical] wq#3294-C2: Frank buys 2 Station P1 Pros for Commander pool backends

## The physical task (this ticket's actual gate)

wq#3294's `DESIGN.md` §C ("≥2 always-on backends on separate hardware") pre-authorized
spend up to 6 units and recommended buying 2 now, revisiting with uptime data after a
month. This ticket is that queued physical act, per the standing instinct that a
Frank-only physical act (buy something, touch hardware, mint a credential on his own
account) gets filed as a ticket, never left as a gate blocking other work.

**No session on this fleet can execute the purchase.** There is no storefront
credential, payment method, or browser reachable from a headless BUILD worker's
sandbox — same class of irreducible blocker as the CF API token mint in wq#2614
(`ops/wq2611-a1/README.md:13`) and the bench power-cycle in `kb/ISSUE-LOG.md` #33. This
is recorded here rather than worked around.

**Decision (made this session, per DESIGN.md's own recommendation — no new judgment
call needed): buy 2 Station P1 Pro units now, not 6.** Two dedicated always-on backends
plus Lumen and Bes gives the pool N+2. Revisit the count after a month of uptime data
per the design doc.

## What ships in this ticket instead

Everything downstream of the purchase that does *not* require the hardware to exist:
the on-arrival runbook below, and a verified example of the pool-membership config the
new nodes join, so the next ticket (post-purchase, with ssh access to each new node) is
a fill-in-the-blanks apply, not a research task.

### On-arrival runbook (per new node, run as a follow-up ticket with real ssh access)

1. **Per-node §3.2 recipe** — clone `ops/wq2608-dc-endpoint-bes-a1/` (the ticket's named
   template) to a new `ops/wqNNNN-dc-endpoint-<node>-a1/` directory. That bundle is
   hand-authored per node (Anubis's `ops/wq2607-dc-endpoint-anubis/` and Bes's
   `ops/wq2608-dc-endpoint-bes-a1/` are structurally different from each other — this
   fleet's convention is a per-node clone, not a shared generator), so don't build a
   templating tool for it; `cp -r` the Bes bundle and substitute:
   - `BES_HOME=/home/bes` → the new node's home dir
   - `User=bes` (in `dc-gateway.service`) → the new node's user
   - `mcp-bes.lumennode.uk` → the new node's hostname
   - `MemoryHigh=3G`/`MemoryMax=4G` — **do not copy these verbatim.** Bes's limits are
     justified by Bes being a *shared* 16G box (HA monitor, mosquitto, Grafana also
     live there — `ops/wq2608-dc-endpoint-bes-a1/README.md:16-19`). A Station P1 Pro
     bought for this pool is a *dedicated* node, so its limits should be set from that
     node's actual verified RAM (`free -h` on the real box), not guessed or copied from
     a shared-box number that doesn't apply. If the dedicated node has headroom closer
     to Lumen's own `desktop-commander-mcp.service` (`MemoryHigh=20G`/`MemoryMax=26G`,
     verified via `systemctl cat` per `ops/wq2608-dc-endpoint-bes-a1/README.md:88-89`),
     use that as the upper reference point instead.
   - Same `apply.sh`/`rollback.sh` structure (idempotent ingress patch, backup-before-
     install, health-check-gated enable, quiet-window-guarded cloudflared restart).

2. **Join the mcpw pool** — append a `backends` entry to `commander-pool.json` (the
   config `commander-pool-failover.service` reads, wq#3294 item B — not yet merged into
   this branch, so not copied here verbatim; schema verified by reading
   `ops/wq3294-commander-ha/pool_failover.py` on commit `bc73c6e5`). `commander-pool.example.json`
   in this directory is a verified-schema starting point with the two backends that
   exist today (Lumen live, Bes staged per wq#2608); the on-arrival ticket appends one
   `{"name": "<node>-dc", "url": "https://<node-hostname>/mcp"}` entry per new P1 Pro
   once its hostname is live, and deploys the result to `/home/bes/etc/commander-pool.json`
   (the path `commander-pool-failover.service` is hardcoded to read).

3. **`commander-pool-failover.service`** — **open question, flag for the on-arrival
   ticket rather than silently resolved here:** the unit's own header comment says it
   "Installs on BES (and later each P1 Pro)", but wq#3294's `DESIGN.md` architecture is
   one central health/failover driver that must "survive the death of the node it fails
   away from" — i.e. one active loop, not one per backend. Running an independent copy
   on every pool node risks duplicate demote/promote hook firing (double pages, and once
   item A — Worker pool-membership edits — lands, racing KV writes) with no stated
   leader-election. Read literally, "and later each P1 Pro" more plausibly means *one of*
   the P1 Pros becomes an available failover *location* for the single active loop
   (today: Bes), not that both run it simultaneously. Don't install and enable the unit
   on more than one node at a time until that's confirmed one way or the other — it's a
   design question, not a deployment mechanics one, and is cheap to get wrong here
   (duplicate pages) versus expensive to debug later.

## Why this ticket has no acceptance probe

`[NO-PROBE: physical: purchase on Frank's account]` — a purchase isn't a thing a test
can assert on. AC1 here is the citation above: this README plus
`commander-pool.example.json` are the real, reviewable artifact this session produced;
the purchase itself is queued for Frank outside this session's reach.
