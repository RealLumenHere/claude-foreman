# wq#3702 — judgment: hathor's tailscale-offline alert (selfheal CLASS 6)

Decider: claude-opus-5 @ high, 2026-10-08, under CLAUDE.md Tier-0 #3 (full autonomy —
decide and act, record the reasoning). Inputs: wq#3696 (shipped `nodes.ignore` entry,
PR #1720, squash `fc1839ad`), wq#3697's CONTRADICT verify-pass
(`reports/wq3697_verify_wq3696_hathor_ignore_blindspot.md`),
`ops/wq3354-fleet-selfheal/sh_nodes.py`, `/home/lumen/bin/wake-dispatch.sh:70-99`,
`lumen_state current hathor`.

## Decision

**None of the three options as posed. Split the signal instead.**

The three candidates the verify-pass offered were: keep `ignore` (shipped), switch to
`notes` (the nephthys pattern), or bound the suppression by duration. Each loses
something that matters:

- `ignore` is unconditional and permanent (`sh_nodes.py:118-120` → `not_fleet`): it
  silences the unfixed cold-boot tailscaled failure forever. That is the defect wq#3697
  found, and it stands.
- `notes` alone keeps the page but files a p1 every single time hathor naps.
  `auto-sleep.timer` poweroffs after 30 min idle and `offline_after_s` is 1800, so any
  work session produces a ticket an hour later, forever. Pure alert fatigue; the page
  would be muted by hand within a week and we would be back to `ignore` with extra steps.
- A duration ceiling cannot work for this node. Frank's `power_policy` is "SHUT DOWN
  AFTER WORK — UP ONLY when actively doing work", so hathor is legitimately dark for
  nights and weekends. Any ceiling short enough to catch a boot race also pages every
  night; any ceiling long enough to survive a weekend catches nothing. Duration is not
  the discriminator.

The discriminator is already written down in the fleet's own code.
`wake-dispatch.sh:96-98` detects the bug as **"awake on LAN but tailnet unreachable"** —
it pings/ssh's the node's LAN address and finds the tailnet dark. That splits hathor's
two states cleanly:

| hathor state | LAN (`lan_ip` 192.168.8.172) | tailnet | meaning | action |
| --- | --- | --- | --- | --- |
| idle poweroff (auto-sleep) | silent | dark | by design, Frank's power policy | nothing |
| cold boot, tailscaled down | answers | dark | wq#3685's unfixed boot race | page |

So: hathor goes into a new `nodes.sleepers` list, not `ignore`, **and** gets a
`nodes.notes` entry. The nap is silent, awake-but-dark still pages, and the page that
fires carries the remediation (`sudo systemctl restart tailscaled`) instead of making
the next worker re-derive it.

## Why this and not a wake-dispatch auto-remediation

Restarting tailscaled from `wake_node()` would fix the one path that already detects the
race, but it is not a substitute: it only covers boots that wake-dispatch itself
initiates, it needs sudo on a remote node, and a failed restart still needs somebody to
notice. CLASS 6 is the fleet's only check that asks "is this node visible on the
tailnet", and it should answer that question for hathor rather than abstain. Auto-remediation
is a worthwhile follow-up on top of a working page, not a reason to drop the page.

## What changed

- `sh_nodes.py` — `nodes.sleepers` + `sleeper_tailnet_grace_s`: a sleeper that does not
  answer on its `lumen_state` `lan_ip` is skipped with a logged reason (no ticket, no
  `not_fleet` mark, so it is re-evaluated every sweep); one that answers while still dark
  on the tailnet files the normal CLASS 6 ticket once the grace window passes. Node
  identity and `lan_ip` are cached in sweep state so re-probing a sleeper every minute
  does not re-run `lumen_state` every minute.
- Grace window (300 s, hathor's own wake is ~115 s end to end): the timer runs
  `OnUnitActiveSec=1min`, so without it a sweep landing mid-boot would page on a node
  that was about to come up on its own. Falling back asleep clears the window.
- A sleeper with no `lan_ip` fact **reports** rather than going quiet — the failure mode
  this ticket exists to prevent is silence, so an unprobeable sleeper is loud and the
  ticket says which one-line config gap to close.
- The ticket hint text. It used to read only "If a node is meant to be off, add it to
  selfheal.yaml nodes.ignore" — which is exactly what wq#3696 did, correctly following
  the one lever the tool advertised. It now names all three levers and what each costs.

## Evidence

`python3 /tmp/fworker-wq3702/probe_hathor_sleeper.py` — shipped `selfheal.yaml` through
shipped `sweep()`, dry-run, pinned inputs:

```
config: sleepers=['hathor'] grace=300s hathor_in_ignore=False
asleep (powered off)         t+  30min  hathor_ticket=False  non_sleeper_ticket=True
asleep (powered off)         t+ 360min  hathor_ticket=False  non_sleeper_ticket=True
asleep (powered off)         t+2880min  hathor_ticket=False  non_sleeper_ticket=True
awake on LAN, tailnet dark   t+  30min  hathor_ticket=False  non_sleeper_ticket=True
awake on LAN, tailnet dark   t+  34min  hathor_ticket=False  non_sleeper_ticket=True
awake on LAN, tailnet dark   t+  35min  hathor_ticket=True   non_sleeper_ticket=True
```

The probe can fail and does discriminate: flipping the LAN answer — the one input the
fix reads — flips the verdict, nephthys (outside the `sleepers` population, so outside
the parameter's default) tickets in every row, and 48 h asleep still files nothing.
`pytest tests/ -q` → 61 passed (4 new, `tests/test_resource_classes.py:330-426`).
Implementation: `sh_nodes.py:95-132` (probe + verdict + ticket detail),
`sh_nodes.py:170-182` (the sweep branch), `sh_nodes.py:196-202` (the hint),
`selfheal.yaml:93-106` (`sleepers` + grace, hathor out of `ignore`),
`selfheal.yaml:110` (`notes.hathor`).

## Follow-up (filed, not blocking)

**wq#3711** — deploy. CLASS 6 runs from `/home/lumen/agents/fleet-selfheal/`, and
`install.sh:53` never overwrites an existing `selfheal.yaml` there, so the live sweep
keeps hathor in `nodes.ignore` until that copy is synced by hand. That path is outside a
build worktree's allowed dirs, so this lane could not do it.

**wq#3710** — `wake-dispatch.sh:96-98` should remediate the boot race it already detects
(restart tailscaled on the node, re-probe, warn only if it still will not come up), not
just log it. Separate change, to a file outside this repo. ISSUE-LOG row 1243 records the
hint-text root cause.
