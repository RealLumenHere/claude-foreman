# wq#4049 — verify wq#4011: OPNsense rewire prep (subnet reconcile, HA disarm gate)

**Verdict: CONFIRM**

PRs: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1990 (content) +
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1992 (filename fix), both merged into
`main` (`35dd43d3` content commit, `5e340d51` rename to the ticket's declared
`deliverable` path). Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh
instance), per wq#4049. Worktree: `/home/lumen/worktrees/wq4049-a1`.

## Scope

wq#4011 (archetype standard-engineering, `deliverable:
/home/lumen/foreman/research/OPNSENSE_REWIRE_PREP.md`) ships a single markdown research
doc reconciling conflicting subnet records (OPNsense LAN / friend's TP-Link / be3600),
writing a disarm/re-arm runbook for the vault HA auto-promote monitor ahead of a future
LAN rewire, and flagging WAN-topology + RAM-spec as non-blocking physical TODOs for
Frank. No code, no live fleet or network change.

## What I checked

1. **Git history of the landed diff.** `git log --oneline --all -- research/OPNSENSE_REWIRE_PREP.md`
   shows two independent branches: `64f3b956`→`c0645ff8` (orphan, never merged — confirmed
   via `merge-base --is-ancestor`, both NOT ancestors of `main`) and `35dd43d3`→`5e340d51`
   (the one actually on `main`). Diffing `35dd43d3` against `64f3b956` shows byte-identical
   content (112 insertions, one new file) — the orphan branch is a discarded duplicate
   attempt, not evidence of a half-landed or conflicting change. The merged diff touches
   exactly one file (plus one pure rename, 0 insertions/deletions) — no script, config, or
   HA-state file is touched, which is itself evidence for the doc's own "no live change"
   claim.

2. **Every citation into live scripts, verified by direct read, not trusted on claim:**
   - `wadjet-ha-monitor.sh:14` (`HA_DIR=/home/lumen/ha`) and `:22`
     (`ENABLE_FLAG="$HA_DIR/AUTO_PROMOTE_ENABLED"`) — exact line match.
   - `wadjet-ha-monitor.sh:147-150` (`--status` mode, `auto_promote` line testing
     `[ -e "$ENABLE_FLAG" ]`) — exact line match.
   - `wadjet-ha-monitor.sh:212-213` (`Ma'at down ${N}x but AUTO_PROMOTE disabled — alert
     only`) — exact line and string match.
   - `wadjet-ha-failback.sh` detached systemd-run convention
     (`--setenv=_DETACHED=1`, `systemd-run --unit="$unit" --collect
     --property=Type=oneshot`, lines 94-102) and `show_status` (`in_recovery`,
     `pg_stat_wal_receiver`, `pg_stat_replication`, lines 106-120) — both match the doc's
     description of the mechanism and the proposed disarm/re-arm commands' shape.
   - `research/NETWORK_PREMORTEM.md:100` — "Every LAN address in `bin/` is in one
     `192.168.8.0/24`" — exact match for the doc's claim that the rewire's target LAN
     (`192.168.8.1/24`) avoids both the TP-Link's and be3600's subnets.
   - Reproduced the doc's own evidence claim: `grep -rln wadjet-ha-monitor.timer research/
     ops/` (pre-this-ticket state, i.e. excluding the new doc) turns up only consumers of
     the timer's status (`FLEET_SERVICE_ATLAS.md`, two watchdog-ticket READMEs, an
     inventory JSON) — none is a disarm/re-arm procedure. The "no runbook existed before
     this ticket" claim holds.
   - `scratch/research/opnsense-post-rewire.md` (the doc's primary source, read in full):
     every number the deliverable repeats from it (192.168.1.1 OPNsense LAN,
     192.168.8.1/24 target, RAM 16GB vs 32GB discrepancy, "opnsense-fw offline 9d",
     §A1 Flint-demotion gate) is quoted correctly and in context.

3. **The one real judgment call in the doc — flagged as such, not fabricated.** §1's
   resolution ("claude_memory's TP-Link entry is a stale conflation with be3600; trust
   FLEET_MAP's `/24`") is inference, not a verified fact, and the doc says so explicitly
   ("far more likely explanation") and queues the actual record fix as a TODO rather than
   auto-editing `claude_memory`/FLEET_MAP — correct under instinct 7 (memory consolidation
   is proposal-only). I could not independently re-verify the referenced FLEET_MAP/
   claude_memory wording myself (both are explicitly external to this repo per the doc's
   own caveat, and outside this worktree's granted paths), but the reasoning from the
   values as quoted in the scratch doc is internally sound: a `/24` grant for a named
   device is a more deliberate record than a single recalled IP, and the collision with
   be3600's independently-sourced address is real either way.

4. **RAM and WAN-topology TODOs are correctly non-blocking,** per instinct 3: both are
   physical facts nobody in this process can confirm, both are queued as TODOs for Frank
   rather than gates, and neither blocks the subnet-reconciliation or HA-gate deliverables.

5. **No live change.** The merged diff is doc-only (confirmed above). `/home/lumen/ha` and
   the HA monitor's live status are outside this worktree's granted paths (sandboxed the
   same way wq#4011's own worker would have been for a `standard-engineering` ticket with
   no HA-mutation need), which structurally corroborates rather than merely asserts "no
   live fleet or network change made."

## Tried to break it

- Checked for a fabricated grep/citation (instinct 8): every line number and quoted
  string reproduced exactly against the live file, not just plausible-looking.
- Checked for an orphaned/abandoned duplicate landing as a sign of a flawed merge: found
  one (`64f3b956` chain), confirmed byte-identical to the merged content and never an
  ancestor of `main` — a discarded retry, not a landed inconsistency.
- Checked whether the "subnet collision" resolution contradicts the rewire plan it leans
  on: it doesn't — `NETWORK_PREMORTEM.md:100` independently corroborates the
  `192.168.8.0/24` target avoiding both conflicting subnets.
- Looked for any mutating action beyond documentation (disarm commands actually run,
  HA flag files touched): none in the diff; the deliverable only *proposes* the SSH
  commands, wrapped and detached per instinct 1/5, never executes them.

No contradiction found. The one inference in the doc is explicitly hedged and routed as a
proposal, not asserted as fact — consistent with the ticket's own instincts.

## Not re-implemented

No code changes made here; this is a read-only verification per archetype 8.
