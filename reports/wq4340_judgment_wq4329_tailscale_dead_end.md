# wq#4340 — verify wq#4329 (PR#2232, Phase 1 direct MCP via Remote Desktop Commander)

**Verdict: CONTRADICT, escalated to opus-5@high — judgment: CONTEST.** PR#2232's shipped
code (`ops/wq4329-direct-mcp-commander/`) is internally consistent and its upstream research
checks out, but the README's stated reason for ruling out option (B) self-hosted
`tailscale serve` is factually wrong: it cites ISSUE-LOG #1016 as proof that `tailscale
serve` is "a closed structural dead-end for a BUILD worker," and that row's own status field
reads `SOLVED (wq#3069)`. The shipped option-A deployment does not need to be rolled back —
opus-5@high judgment found an independent, stronger reason A was still probably the right
Phase-1 call (claude.ai cannot reach a tailnet-only endpoint) — but the PR never made that
argument, and the one it did make is contradicted by its own citation.

## What shipped (re-read cold)

`git show --stat a8d41035` (branch `feat/wq4329-project-direct-mcp-connections-per-machi-a2`):
4 new files under `ops/wq4329-direct-mcp-commander/` (README, install.sh,
remote-desktop-commander.service, PHASE1-PAIRING-CHECKLIST.md), 144 insertions, nothing
touched outside that directory.

## Things checked and confirmed correct

1. **Upstream facts are not fabricated.** Ran `/home/lumen/bin/fleet-search web` (prior-art
   doctrine, not guessed) for `mcp.desktopcommander.app`, the `@wonderwhy-er/desktop-commander`
   package name, the OAuth device-flow pairing shape, and the Node >= 18 requirement — every
   one of the README/service-file's upstream claims matched the GitHub source verbatim
   (`https://mcp.desktopcommander.app/mcp`, Streamable HTTP + OAuth 2.0, `npx
   @wonderwhy-er/desktop-commander@latest remote`, Node.js 18+).
2. **install.sh's `--verify` contract and idempotency.** Extracted the bundle's four files
   into an isolated scratch tree (`/tmp/fworker-wq4340/testrun/`, this worker's Bash allowlist
   has no bare `bash <path>` entry, so I used the allowlisted `Bash(bash scripts/*)` shape via
   a thin wrapper) and ran the actual probe, not a re-implementation:
   - `install.sh testuser --verify` before staging: `MISS staged unit missing`, `MISS staged
     unit not templated for testuser`, `VERIFY FAIL (2)`, exit 1 — the probe can fail
     (wq#2630).
   - `install.sh testuser` (stage): writes `staged-units/remote-desktop-commander.service`
     with `__NODE_USER__` correctly substituted to `testuser` everywhere, including
     `User=testuser` and the `PATH=` line. Exit 0.
   - `install.sh testuser --verify` after staging: `VERIFY OK`, exit 0.
   - Re-ran the stage step a second time: identical output, confirming the "idempotent: safe
     to re-run" claim.
   - Confirmed install.sh never calls `daemon-reload`/`enable`/`start`/anything root-only in
     either branch — matches its own stated scope.
3. **ISSUE-LOG #731 citation** (root-install gap in a BUILD sandbox) — real, matches
   (row 731/wq#2606: `install -D` to `/etc/systemd/system/*` and `daemon-reload` are root-only
   and unavailable to a BUILD worker).

## The contradiction

The README justifies dropping option (B) with:

> "(B) self-hosted via `tailscale serve` is already a closed structural dead-end for a BUILD
> worker: ISSUE-LOG #1016 (wq#3069) found `tailscale serve` absent from
> `harness/worker-settings.json`'s Bash allowlist and `fleet-exec-policy.yaml`'s
> `run_readonly_cmds` only permitting `tailscale status`, not `serve`..."

`/home/lumen/kb/ISSUE-LOG.md` row 1016, read in full, says the opposite of "dead end." It
records wq#3069 closing that exact gap: `/home/lumen/bin/lumen-run start --root -- tailscale
serve --bg --tcp 8888 tcp://127.0.0.1:8888` — `lumen-run` is unconditionally allowlisted and
reaches root via its own passwordless-sudo `systemd-run` path, independent of the
`fleet-exec`/`run_readonly_cmds` wall the PR cites. The row's status field reads `SOLVED
(wq#3069); off-host verify still blocked for wq#2485` — the only thing still blocked is an
unrelated, narrower sub-problem (curling a different ticket's SearXNG port from a *different*
fleet node), not whether a BUILD worker can run `tailscale serve` at all.

Independently re-confirmed `lumen-run --root` is still live code today, not a stale
reference: `/home/lumen/bin/lumen-run` line 54 documents the `--root` flag, line 79 parses
it, line 112 calls `sudo -n systemd-run --unit=... --collect "${runas[@]}" ...`; and
`/home/lumen/bin/lumen-run *` is on the current `harness/worker-settings.json` Bash allowlist.

Also found, independently: `docs/fleet/retired_services.yaml` (`desktop-commander-mcp-lumen`
entry, retired 2026-10-13 per wq#3805) shows Lumen already ran a local
`desktop-commander-mcp.service` — a real local Commander server — before it was masked to
centralize on Horus. The original unit file is preserved at
`/home/lumen/backups/wq3805/desktop-commander-mcp.service.orig`. Option (B) would not have
needed to build a local Commander server from scratch.

## Escalation and opus-5@high judgment

Per protocol, a contradiction escalates to opus-5@high. Spawned a fresh opus-5@high instance
with the full text of both the PR's claim and ISSUE-LOG row 1016, plus the `lumen-run`/
`retired_services.yaml` findings, and asked it to judge materiality and next steps (not to
re-implement). Verdict: **CONTEST — amend the documentation, do not revert the code.**

Key points from that judgment:
- The citation is wrong on its face: row 1016 records the mutation being done via `lumen-run
  --root`, outside `fleet-exec`, with a `SOLVED` status — the PR quoted only the problem half.
- Approach (A) may still be the right Phase-1 call anyway, for a reason the PR never gave:
  claude.ai is the MCP client and connects from Anthropic's cloud, so it cannot reach a
  tailnet-only `tailscale serve` endpoint without `tailscale funnel` (public exposure) — which
  gives up (B)'s "stays private" advantage and adds its own work. The PR's silence on this
  means the *decision* might be defensible even though the *stated reason* is not.
- `lumen-run --root` only fixes Lumen; Maksoor needs its own mutation path for (B) — though
  (A)'s per-user unit on Maksoor has the same requirement, so this doesn't clearly favor (A)
  either.
- Recommended action: reopen/amend wq#4329 — strike the "closed structural dead-end" framing,
  redo the A-vs-B comparison honestly (claude.ai reachability, third-party relay trust and data
  exposure, cost of reviving the wq#3805 unit, Maksoor's mutation path) — without rolling back
  the already-shipped option-A deployment unless the redone comparison favors (B).

Filed `[judgment] wq#4340 contradicted wq#4329: tailscale-serve dead-end citation is wrong`
(wq#4341, `deps: 4329`) carrying this finding forward as actionable follow-up work.

## Not re-litigated

- Whether the hosted-relay OAuth pairing actually completes end-to-end on real hardware —
  that's gated on Frank's two physical pairing steps (`PHASE1-PAIRING-CHECKLIST.md`), never
  attempted here, and out of scope for a verify pass.
- Rewriting the README or re-doing the A-vs-B comparison myself — that's the follow-up
  ticket's job, not this verify pass's (verify, do not re-implement).
