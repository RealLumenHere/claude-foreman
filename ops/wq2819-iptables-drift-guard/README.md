# wq#2819 — lumen-iptables-drift-guard

`/etc/iptables/rules.v4` lags the live ruleset by a class of rules that keep
coming back: raw-table `PREROUTING` DROPs guarding loopback-only
(`-d 127.0.0.1/32 ! -i lo`) and bridge-scoped (`-d 172.x/32 ! -i br-*`)
container port publishes, plus the matching `DOCKER` chain accepts. wq#2808
found five of these plus one `DOCKER` accept live but absent from the 06:00Z
save (`ops/wq2808-tailnet-raw-lockdown/README.md:45-50`).

## Root cause
These rules are **dockerd's own**, not a Lumen script. Every container
published with a host-IP-scoped bind (`-p 127.0.0.1:PORT:PORT`, or any
bridge-network publish) gets one raw-table guard rule plus `DOCKER`-chain
accept/DNAT entries, and dockerd re-inserts them whenever it reconciles
container networking — `compose up`, a container restart, or a daemon
restart. dockerd never calls `netfilter-persistent`, so the persisted
snapshot silently falls behind after every such event, with no fixed
schedule. `ops/wq2808-tailnet-raw-lockdown/rules.v4.staged` (wq#2808's live
capture) has dozens of these `-A PREROUTING -d <host-ip> ! -i <lo|br-*> ...
-j DROP` lines, one per published port on this box — far more than any
hand-written script would produce, and each one lines up with a `DOCKER`
chain accept/DNAT for the same IP:port
(`ops/wq2808-tailnet-raw-lockdown/rules.v4.staged:13-42,163-165,333,335`).
wq#2769's `actual-budget` fix (rebinding a container to `127.0.0.1:5006`)
is exactly the trigger: rebinding recreated the container, and dockerd added
the `5006` guard rule as a side effect, live only, with nothing in that
ticket's flow calling `netfilter-persistent save`
(`foreman/reports/2769_ghost_ollama_ports_residuals.md:51`).

wq#2808's `apply.sh` already runs `netfilter-persistent save` once at the
end of its own apply — that closes the gap for a moment, but the next
container reconcile (which happens on Lumen's own schedule, not ours) opens
it again. This is the same class as wq#2808's `ts-input` finding (a daemon
that owns its own iptables state and never persists it), different daemon,
and it needs the same answer: reconcile continuously, not once.

## What this adds
`iptables_drift_guard.py run`, on a 15-minute timer:
1. Diffs `iptables-save` (live) against `/etc/iptables/rules.v4` (persisted),
   normalizing away comment lines and packet/byte counters (`[pkts:bytes]`
   churns every packet and carries no rule identity).
2. If they match, exits 0, no-op.
3. If they differ, runs `netfilter-persistent save`, re-diffs to confirm the
   save actually closed the gap, and sends one `ntfy-send` alert either way
   (`lumen-alerts`: "saved" on success, "NOT resolved" — a page, not a
   silent no-op — if the re-check still shows a difference, e.g. dockerd
   rewrote a rule mid-run).

Read-only against the live ruleset: the only mutation is
`netfilter-persistent save`, the same call wq#2808's `apply.sh` already
makes by hand.

## Install / rollback
```
lumen-run start --root -- bash /home/lumen/foreman/ops/wq2819-iptables-drift-guard/install.sh
python3 /home/lumen/services/lumen-iptables-drift-guard/iptables_drift_guard.py run --dry-run
```
Rollback: `systemctl disable --now lumen-iptables-drift-guard.timer`, remove
`/etc/systemd/system/lumen-iptables-drift-guard.{service,timer}` and
`/home/lumen/services/lumen-iptables-drift-guard/`. Nothing else is mutated.

## Tests
`python3 -m pytest ops/wq2819-iptables-drift-guard` — the diff and
save/re-verify/alert logic against `fixtures/live_rules.v4` (wq#2808's
committed live capture) and `fixtures/stale_rules.v4` (the same file with
exactly the six dockerd-owned guard rules this ticket names removed),
pinned fixtures rather than a live mutable file, per wq#2630. Covers: the
drift class itself, counter churn *not* being mistaken for drift, the
reverse direction (persisted-only rules), and `run()` paging instead of
claiming success when a save doesn't actually close the gap.
