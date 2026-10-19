# wq#4329 — Direct MCP connections: per-machine Commander replacing the Horus SSH hop

## Current path (what this replaces)

Per `docs/fleet/retired_services.yaml:302-327` (wq#3805, 2026-10-13): Lumen's own
local Desktop Commander (`desktop-commander-mcp.service`) was stopped and masked
because Commander moved to run centrally on Horus (`mcp-proxy`), reached through
the `mcpw.lumennode.uk` Cloudflare Worker. Every fleet node is therefore worked on
by Horus SSHing out to the target — one shared backend, one SSH hop per command,
one 90s ceiling, one outage domain (`ops/p1-commander/mcpw-cutover-plan.md` already
treats that single-backend chokepoint as the reason the P1 primary/backup HA
cutover exists).

## Decision: Phase 1 ships approach (A), hosted Remote Desktop Commander relay

**Amended 2026-10-19 (wq#4341, following wq#4340's verify-pass CONTRADICT / opus-5@high
CONTEST on the paragraph this replaces).** The original version of this section claimed
(B) self-hosted via `tailscale serve` was "already a closed structural dead-end for a
BUILD worker," citing ISSUE-LOG #1016 (wq#3069). That citation was wrong: row 1016's own
status field reads `SOLVED (wq#3069)` — the gap it describes (`tailscale serve` absent
from the Bash allowlist, `fleet-exec-policy.yaml`'s `run_readonly_cmds` only permitting
`tailscale status`) was closed via `/home/lumen/bin/lumen-run start --root -- tailscale
serve ...`, which reaches root through its own passwordless-sudo `systemd-run` path
(`lumen-run:112`) independent of fleet-exec entirely. `lumen-run --root` is still live and
unconditionally allowlisted today (`harness/worker-settings.json:19`). The only thing row
1016 leaves open is an unrelated off-host-`curl` sub-problem scoped to a different ticket
(wq#2485), not whether `tailscale serve` can run at all. So (B) was never a structural
dead-end for Lumen, and the PR's stated reason for picking (A) does not hold.

The honest A-vs-B comparison, redone:

- **claude.ai reachability is the real blocker for (B) on this project, not the allowlist.**
  claude.ai is the MCP client and it connects from Anthropic's cloud, not from inside the
  tailnet. A `tailscale serve` endpoint is tailnet-only; reaching it from claude.ai would
  need `tailscale funnel` instead, which publishes the endpoint to the public internet —
  giving up the exact "stays private" property that made (B) worth considering over a
  hosted relay. This is the reason the original PR should have given and didn't.
- **Third-party trust and data exposure cut both ways, not cleanly toward (A).** (A) routes
  every MCP command and its output through a third-party SaaS relay
  (`mcp.desktopcommander.app`) that neither Frank nor this fleet controls. (B) via
  `tailscale funnel` terminates on Tailscale's own funnel infrastructure — a vendor the
  fleet already trusts tailnet-wide for other traffic — and skips the added
  desktop-commander.app relay, but it does so by making the endpoint publicly reachable,
  which is its own exposure. Neither option is "no third party"; they trade one exposure
  for a different one.
- **Reviving (B) locally is not a from-scratch build.** `docs/fleet/retired_services.yaml`
  (`desktop-commander-mcp-lumen` entry, line 319) shows Lumen already ran a local Commander
  server, `desktop-commander-mcp.service`, before it was masked on 2026-10-13 (wq#3805) to
  centralize on Horus. The original unit file is preserved at
  `/home/lumen/backups/wq3805/desktop-commander-mcp.service.orig`. Reviving it is an
  unmask-and-restart of an existing unit, not writing a new server — a materially lower
  cost than the original README implied by calling (B) "closed."
- **Maksoor still needs its own mutation path.** `lumen-run --root` is Lumen-local; it does
  not give Maksoor a root-owned way to run `tailscale serve`/`funnel` or to unmask a local
  Commander unit there. (A)'s per-user systemd unit on Maksoor has the equivalent
  requirement (install.sh does not escalate privilege on either machine), so this point
  doesn't clearly favor either approach — it's a open cost on both sides, not unique to (B).

**Net call: Phase 1 still ships (A), hosted Remote Desktop Commander relay** — but for the
claude.ai/funnel reachability reason above, not because (B) was structurally blocked. The
already-shipped option-A deployment (this bundle) is not being rolled back: the redone
comparison does not favor (B) strongly enough to justify discarding working Phase 1 code
over a documentation error. Recorded here per the full-autonomy instinct.

Verified against the upstream repo (`fleet-search web`, 2026-10-19 — doctrine's
prior-art step, not guessed):

- Hosted endpoint: `https://mcp.desktopcommander.app/mcp` (Streamable HTTP, OAuth
  2.0), dashboard at `mcp.desktopcommander.app`. Source: GitHub —
  `desktop-commander/remote-desktop-commander` README.
- Per-machine agent: `npx @wonderwhy-er/desktop-commander@latest remote`, requires
  Node.js >= 18.0.0 on that machine. First run is an OAuth device-flow pairing
  (browser "Verify your device" page, code must match the terminal) — a one-time,
  per-machine, interactive step.
- Once paired, the AI assistant adds Remote Desktop Commander as an MCP connector
  and reaches the paired device directly — no SSH hop, no shared backend, no
  per-request 90s ceiling imposed by a third hop.

## What's in this bundle

- `remote-desktop-commander.service` — per-machine systemd unit (user service,
  `__NODE_USER__` templated per fleet convention, see
  `ops/p1-commander/dc-gateway.service` for the existing unit style this matches)
  that keeps `npx @wonderwhy-er/desktop-commander@latest remote` running after the
  one-time pairing, so the terminal doesn't have to stay open.
- `install.sh` — idempotent: checks Node >= 18, writes the unit with the real
  username substituted, does **not** attempt the OAuth pairing itself (that needs
  a human in a browser — see below) and does not `daemon-reload`/`enable`/`start`
  anything (this BUILD sandbox has no systemd privilege verb for a new unit path,
  same structural gap as ISSUE-LOG #731).
- `PHASE1-PAIRING-CHECKLIST.md` — the two physical tasks this phase actually needs
  a human for, queued per the full-autonomy instinct (a physical act, not a gate).

## Phase 1 scope (this ticket)

Lumen (100.117.66.102) and Maksoor (100.81.204.74, `TOOL_REGISTRY.md:9`) only.
Phase 2 (Spectrio RE host) and Phase 3 (rest of fleet) are follow-on tickets once
Phase 1's pairing is verified end-to-end — not attempted here.
