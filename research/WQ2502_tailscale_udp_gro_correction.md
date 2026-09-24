# wq#2502 — Tailscale UDP GRO forwarding flag correction

Origin: wq#2466 digest of Frank's fleet doc (`Orchestation 2.md` L954-996, section
"Tailscale UDP GRO / GSO Optimizations"), digest row R28. The wq#2466 digest itself
(`research/DIGEST_Orchestation_2_md.md`) had not landed as of this ticket, so this
note stands alone until that digest merges and can cite it directly.

## The correction

The source doc's L980 command is:

    ethtool -K IFACE rx-udp-gro-forwarding on rx-gro-list on

This is backwards on the second flag. Verified independently (fleet-search web,
2026-09-24, four independent sources, all agreeing):

- Tailscale's own Performance best practices page
  (tailscale.com/docs/reference/best-practices/performance): `ethtool -K $NETDEV
  rx-udp-gro-forwarding on rx-gro-list off`
- OpenWrt Wiki, Tailscale page (openwrt.org/docs/guide-user/services/vpn/tailscale/start):
  same pairing, `rx-gro-list off`
- A Fedora Discussion thread on Tailscale offload tuning surfaces a "Warning: UDP GRO
  forwarding is suboptimally configured" check that fires specifically when
  `rx-gro-list` is left `on`
- A community gist walking through the same tuning cites the identical Tailscale KB
  pairing

Correct command for any node that forwards tailnet traffic:

    ethtool -K IFACE rx-udp-gro-forwarding on rx-gro-list off

The mechanism (Tailscale batches small UDP packets into 64 KiB super-packets on
Linux 6.2+ / Tailscale 1.54+, cutting the per-packet userspace context-switch cost
in wireguard-go) is real and correctly described in the source doc; only the second
flag's value is wrong.

## Scope: forwarding nodes only

This tuning only helps nodes that forward tailnet traffic — subnet routers and exit
nodes — not ordinary mesh members. The source doc applied it fleet-wide on faith;
this ticket's acceptance criteria require the opposite: identify which nodes actually
advertise routes or serve as an exit node (via `tailscale status`/`prefs`) before
touching anything, and apply the flags only where a measured iperf3 delta justifies it.

## Investigation status: blocked in this dispatch

This ticket carries no `needs:ssh(<node>)` capability tag, so `fworker.py`'s
`resolve_exec_grant` minted no grant for this attempt — `fleet-exec grant` returns
`DENY [grant:grant_missing_env]`. Bare `ssh`/`tailscale`/`ethtool`/`iperf3` are not on
`harness/worker-settings.json`'s Bash allowlist either (same class of gap as
ISSUE-LOG #71/#114), so this build worker has no path — live or broker-mediated — to
run `tailscale status` against any fleet node in this attempt.

No static evidence in-repo substitutes for it either: `research/FLEET_MAP.md` and
`foreman/research/` do not name any node as a subnet router or exit node. Guessing
"no nodes forward" to close this cheaply, or guessing a node does forward to apply
the flags, would both violate the "never guess a path/config value/root cause"
doctrine — recorded instead of guessed. See ISSUE-LOG #661.

Even a correctly-tagged grant would only get partway: `fleet-exec`'s v1
`run_readonly_cmds` allowlist includes `tailscale status` but not `ethtool` or
`iperf3`, and persisting the flags in a systemd unit needs the elevated-privilege
`tee`-into-`/etc/systemd/system/` verb, which `fleet-exec-policy.yaml` documents as a
v2 verb the CLI does not expose yet. Steps 2 (before/after iperf3, apply the flags)
and 3 (persist past a power cycle) of this ticket are therefore not executable by any
build worker in the current harness, tagged or not — they need either direct operator
access or a broker capability that does not exist yet.

## What this ticket lands

Just the doc correction above, recorded here since `DIGEST_Orchestation_2_md.md`
isn't merged yet to carry it. Filed wq#2503 as the follow-up, tagged
`needs:ssh(...)` for each of the seven nodes `fleet-exec-policy.yaml`'s `ssh:`
allowlist currently covers (lumen, isis, bes, anubis, maat, sobek, hathor — thor is
deliberately absent, never touched), asking `tailscale status`/`prefs` on each to
answer the forwarding-node question for real. Nodes outside that seven-node roster
remain unreachable through the broker regardless of tagging, which wq#2503 states
explicitly rather than implying full fleet coverage.

Do not apply the flags fleet-wide without a measured delta per node — the source
doc's fleet-wide-on-faith approach is exactly the mistake this ticket exists to
correct.
