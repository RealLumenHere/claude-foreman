# wq#2496 — Tailscale UDP GRO/GSO: before/after measurement (and the doc flag correction)

Source: `research/DIGEST_Orchestation_2_md.md` T3 (wq#2466 digest of Frank's
`Orchestation 2.md` L954-996, "Tailscale UDP GRO / GSO Optimizations").

## The doc is wrong — do not paste it

O2 L980 reads:

    ethtool -K enp3s0 rx-udp-gro-forwarding on rx-gro-list on

This is backwards. `rx-gro-list` takes precedence over `rx-udp-gro-forwarding` and
*limits* the effect of UDP GRO, reducing throughput instead of improving it
(tailscale.com/blog/quic-udp-throughput, 2023-11-16). Tailscale's own current
guidance (tailscale.com/docs/reference/best-practices/performance, checked
2026-09-30 via `fleet-search web`) is:

    ethtool -K $NETDEV rx-udp-gro-forwarding on rx-gro-list off

This correction already landed once, independently, in
`research/WQ2502_tailscale_udp_gro_correction.md` (wq#2502, 2026-09-24). This
doc does not re-derive it; it re-confirms it is still current (Tailscale's page
carries a "Dec 4, 2025" revision date, unchanged) and folds it into this
ticket's own record so `research/` doesn't need cross-referencing to get the
right command.

## Scope: the tuning only applies to forwarding nodes

The Tailscale performance-best-practices page frames the whole recommendation
as conditional: *"If a Linux device is acting as an exit node or subnet
router, ensure..."* (search snippet, tailscale.com/docs/reference/best-practices/performance,
retrieved 2026-09-30). `rx-udp-gro-forwarding` optimizes the kernel's
`ip_forward` path — the packet path a subnet router or exit node actually
uses when relaying tailnet traffic to/from a LAN or the public internet.
Ordinary mesh peers terminate tailscale traffic locally in `tailscaled`
(wireguard-go or in-kernel WireGuard) without an `ip_forward` hop, so this
specific flag pair has no packet path to act on for them. This matches wq#2502's
independent finding (its "Scope: forwarding nodes only" section) and is
reconfirmed here against the live doc rather than assumed from the prior note.

**Consequence for this ticket's step 1** ("pick two nodes that actually move
bytes over tailscale"): moving bytes over tailscale is not the qualifying
condition — *forwarding* tailnet traffic (subnet-router or exit-node role) is.
A high-throughput pair of ordinary mesh peers would not measure a meaningful
GRO/GSO delta from this flag pair even with the harness access to run the
test, because neither side's kernel is on the forwarding path this flag
tunes.

## Is there a forwarding node in the fleet to test on?

Already answered, live, by wq#2503 (2026-09-24, `research/WQ2503_tailscale_forwarding_audit.md`):
`tailscale status` was run via a `needs:ssh(...)`-granted `fleet-exec` call
against all seven SSH-allowlisted fleet nodes (lumen, isis, bes, anubis, maat,
sobek, hathor — thor excluded, never touched). A case-insensitive grep for
`exit`/`route` across the full ~29-peer tailnet listing, on all seven runs,
returned zero matches: no peer anywhere in the tailnet — not the seven target
nodes, not any other peer — carries a route-advertisement or exit-node
annotation. **No node in the reachable fleet forwards tailnet traffic.**

Re-checked as part of this ticket (2026-09-30, static-evidence pass, no live
grant available to this dispatch — see below): `research/FLEET_MAP.md` names
no node as a subnet router or exit node, consistent with the live finding six
days prior. No fleet-exec-policy or FLEET_MAP change in the interim suggests
this has changed.

## Can this dispatch run the live measurement anyway?

No, re-verified live in this attempt (2026-09-30), same shape as wq#2502:

- This ticket's `needs` field is empty (`/home/lumen/bin/wq show 2496 --json`).
  No `needs:ssh(<node>)` tag means `fworker.py`'s `resolve_exec_grant` mints no
  grant for this dispatch. Confirmed: `fleet-exec grant` returns
  `DENY [grant:grant_missing_env]`.
- Bare `ssh` is not on `harness/worker-settings.json`'s Bash allowlist.
  Confirmed: `ssh lumen@100.117.66.102 'echo test'` was refused —
  "This Bash command contains multiple operations... requires approval."
- Even a correctly-tagged grant would not reach `ethtool`/`iperf3`:
  `harness/fleet-exec-policy.yaml`'s `run_readonly_cmds` (line ~200) still
  lists only `"tailscale status"`, no `ethtool`, no `iperf3` — unchanged from
  wq#2502's finding. Persisting flags past a reboot needs a `tee`-into-`/etc/systemd/system/`
  verb the policy file documents as v2, still not exposed by the CLI.

So both preconditions for this ticket's steps 2-4 (a forwarding node to test
on, and a harness path to run `ethtool`/`iperf3` against it) are absent, and
the second one is independent of the first — it would block the measurement
even if a forwarding node existed.

## Before / after / revert table

| Node pair | Role | iperf3 throughput (before) | CPU% (before) | `ethtool -K` applied | iperf3 throughput (after) | CPU% (after) | Reverted, back to baseline? |
|---|---|---|---|---|---|---|---|
| — | — | not run | not run | not applied | not run | not run | n/a |

No row is populated because no eligible node exists (previous section) and no
build-worker execution path exists to run `ethtool`/`iperf3` against a fleet
node even if one did (section above). Populating this table with invented
numbers, or with numbers from a node that doesn't forward tailnet traffic
(which would measure something other than what the ticket asks about), would
both be guessing against explicit doctrine — recorded instead.

## Verdict: CLOSE

**CLOSE**, not ADOPT. Two independent reasons, either one sufficient on its own:

1. No node in the fleet that this harness can reach (the seven SSH-allowlisted
   nodes, per wq#2503's live audit) forwards tailnet traffic, so the
   `rx-udp-gro-forwarding` / `rx-gro-list` tuning has no packet path to act on
   anywhere in the fleet today.
2. Independent of (1): no build-worker execution path exists to run
   `ethtool`/`iperf3` against any fleet node in the current harness (no Bash
   allowlist entry, no `fleet-exec` policy entry, and this ticket carries no
   `needs:ssh` capability tag to mint a grant even for the read-only pieces
   that *are* on the policy allowlist).

Per the ticket's own instruction, this is a valid, cheap outcome, not a
blocked one: "Only on ADOPT, file the follow-up that makes it persistent" —
since the verdict is CLOSE, no persistence follow-up is filed. If a fleet node
is ever configured as a subnet router or exit node in the future, or if
`fleet-exec-policy.yaml` grows `ethtool`/`iperf3`/`tee`-unit-write verbs, this
verdict should be revisited — the tuning itself, `rx-udp-gro-forwarding on
rx-gro-list off`, is legitimate Tailscale guidance and not the reason for the
CLOSE.

## What this ticket does NOT do

- Does not apply the `ethtool` flags anywhere (no eligible node, no execution
  path — instinct 3 "decide and act" does not license acting past a hard
  execution boundary).
- Does not touch thor (instinct 2; also outside the seven-node SSH allowlist
  entirely).
- Does not re-run wq#2503's live `tailscale status` audit — it already
  answered the forwarding-node question for the only nodes this harness can
  reach, six days ago, with a clean zero-match result across the whole
  tailnet; re-running it today would need the same `needs:ssh` tag this
  ticket doesn't carry, and would not be expected to find a different answer
  absent a fleet topology change nothing in `research/FLEET_MAP.md` or the
  ISSUE-LOG since 2026-09-24 indicates happened.
- Does not build a second iperf3 harness for wq#2490 to share — there is
  nothing to share it with yet, since neither ticket's live measurement step
  is reachable by a build worker right now.
