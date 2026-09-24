# wq#2503 — live tailscale forwarding-node audit

Follow-up to wq#2502 (`research/WQ2502_tailscale_udp_gro_correction.md`), which
corrected the UDP GRO `ethtool` flag pairing but could not run step 1 of its
acceptance (identify which fleet nodes actually forward tailnet traffic) because
that dispatch carried no `needs:ssh` tag. This ticket carried the tag, so
`fleet-exec grant` minted a live `run` grant for all seven nodes
`harness/fleet-exec-policy.yaml`'s `ssh:` allowlist covers.

## What ran

`fleet-exec run <node> -- tailscale status` against each of: lumen, isis, bes,
anubis, maat, sobek, hathor (thor excluded — never on the allowlist, never touched).
All seven calls succeeded and returned the full tailnet peer listing (~29 lines
each, consistent across all seven runs — not truncated).

`fleet-exec run lumen -- tailscale prefs` was also attempted, since the ticket
described `tailscale prefs` as being on fleet-exec's read-only allowlist alongside
`status`. It is not: the broker denied it outright —
`DENY [policy:not_readonly]: 'tailscale prefs' not on run_readonly_cmds` — and
`harness/fleet-exec-policy.yaml`'s `run_readonly_cmds` list confirms only
`"tailscale status"` is present, no `prefs` entry. Logged as
`/home/lumen/kb/ISSUE-LOG.md` #664 rather than guessed around or retried.

## Finding

`tailscale status` output carries a route/exit-node annotation on a peer's line
when that peer advertises subnet routes or offers itself as an exit node. Across
all seven nodes' full status output, a case-insensitive grep for `exit` and
`route` returned zero matches — not on any of the seven target nodes' own entries,
and not on any other peer in the ~29-device tailnet either.

**None of the seven nodes (lumen, isis, bes, anubis, maat, sobek, hathor) forward
tailnet traffic.** No subnet router, no exit node, among this set.

This is the "none forward" branch the ticket names as a valid, cheap outcome.
Per the ticket's own instruction, changing nothing: the UDP GRO `ethtool` tuning
from wq#2502 only helps forwarding nodes, and there are none in the reachable set
to apply it to.

## Caveat: prefs-only fields unverified

`AdvertisedRoutes` and `ExitNodeOption` are `tailscale prefs` fields, not `status`
fields. `status`'s per-peer annotation is the closest live signal this worker
could reach given the `prefs` denial above, and it is consistent across the whole
tailnet (no peer anywhere shows the annotation, not just the seven target nodes),
but it is inference from `status`, not a direct read of `prefs`. If a future
dispatch adds `tailscale prefs` to `run_readonly_cmds` in
`harness/fleet-exec-policy.yaml`, re-running against these seven nodes would
upgrade this from inferred to authoritative — expected to confirm the same
"none forward" result given `status` shows no exceptions anywhere in the tailnet.

## Disposition

wq#2502 steps 2-3 (iperf3 before/after, apply flags, persist past a power cycle)
remain moot: there is no forwarding node in the reachable seven to apply them to.
Independent of that, those steps were never executable by this harness anyway —
`fleet-exec`'s v1 `run` verb has no `ethtool`/`iperf3` entries and no
unit-file-write verb — so this ticket does not attempt them.

Closing wq#2503 with this finding. No fleet config changed.
