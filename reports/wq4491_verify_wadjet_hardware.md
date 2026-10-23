# wq#4491 — verify whether wadjet is a Mac Pro before any retirement

## Status: partial — ssh_user resolved and ingested; hardware model NOT verified (capability gap)

## What the ticket asked
wadjet (192.168.8.173, ollama host) was swept into a retired-hardware list and an
old handoff grouped it with Macs, but `lumen_state` carried no `hardware` fact for
it, and `lumen` user ssh is denied on `.173`. Ask: find the right ssh user, read
`/sys/class/dmi/id/product_name` on wadjet, ingest a hardware fact — read-only, do
not retire wadjet or touch its `ha_auto_promote` fact.

## Found: ssh user
`python3 /home/lumen/bin/fleet-ssh-check --nodes wadjet --json` ran a real
BatchMode key-auth ssh to wadjet and executed `hostname`:

```
"name": "wadjet", "user": "wadjet", "ip": "100.116.217.15",
"lan_ip": "192.168.8.173", "state": "ok"
```

Key auth works as user `wadjet`, not `lumen` — matches the mgmt-path map in
`/home/lumen/bin/fleet-auth-ssh-probe.sh` (`wadjet wadjet@100.116.217.15`).
Ingested: `wadjet.ssh_user = wadjet` (observed, source fleet-ssh-check, 2026-10-23).

## Blocked: reading `/sys/class/dmi/id/product_name`
This BUILD lane has no execution path to run an arbitrary read command on wadjet,
for two independent reasons (same capability-gap class as ISSUE-LOG #740/#751/#753/
#1064):

1. `fleet-exec run wadjet hostname` → `DENY [grant:grant_missing_env]`. This
   ticket's dispatch carries no `needs:ssh(wadjet)` tag, so
   `fworker.resolve_exec_grant` never set `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA`
   for this attempt (confirmed: both unset in this process's environment).
2. Independently, `wadjet` is not on `fleet-exec-policy.yaml`'s `allow.ssh` list
   (`harness/fleet-exec-policy.yaml:192`: `[lumen, isis, bes, anubis, maat, sobek,
   hathor, ptah, sekhmet, horus, neith, khnum, maksoor]`) — tagging the ticket
   would not have been sufficient on its own.
3. This lane's Bash allowlist (`harness/worker-settings.json`) has no `Bash(ssh *)`
   entry, so there is also no raw-ssh fallback.

Per precedent (ISSUE-LOG #753), a worker does not unilaterally widen
`fleet-exec-policy.yaml`'s `allow.ssh` — adding a live physical Mac Pro candidate
to a remote-exec allowlist is real blast radius, not a rubber-stamp edit, and the
file's own header says it "lands ONLY by PR into foreman."

**I did not ingest a `wadjet.hardware`/`model` fact.** Fabricating a value (e.g.
copying the "Mac Pro 6,1" string that already appears, unverified, in
`wadjet.wake_procedure`'s prose) would be exactly the fabricated-evidence failure
mode Tier-0 instinct #8 warns about. The Mac Pro classification remains
**UNVERIFIED** from this lane.

## Untouched (as instructed)
- `wadjet.ha_auto_promote` — still `ARMED and pointed at SOBEK, not Wadjet`,
  unchanged by this ticket.
- wadjet was not retired, disabled, or otherwise modified. It remains ONLINE
  serving ollama on `192.168.8.173`.

## What the next lane needs to finish this
1. `wq edit 4491 --needs ssh(wadjet)` (or equivalent) to set
   `FOREMAN_GRANT_FILE`/`FOREMAN_GRANT_SHA` for a resumed attempt, AND
2. a reviewed PR adding `wadjet` to `fleet-exec-policy.yaml`'s `allow.ssh` list, then
3. `fleet-exec run wadjet "cat /sys/class/dmi/id/product_name"`, then
   `lumen_state.py ingest wadjet hardware "<value>"`.

Recorded as ISSUE-LOG row (see `kb/ISSUE-LOG.md`) for the next worker that hits
this same gap on wadjet specifically.
