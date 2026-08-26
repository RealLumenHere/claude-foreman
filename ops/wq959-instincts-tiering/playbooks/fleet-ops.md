# PLAYBOOK: fleet-ops (v1, 2026-08-26, wq#959)

Domain rules moved verbatim from `instincts.yaml` (tier-2 restructure, R1 §6). Trigger:
mutating fleet state — node ops, service stops, backfills, Gatus edits, tag_sections,
manual fworker spawns. The tier-2 index entry `fleet-ops-domain` points here.

## State store first
(added ≤2026-08-13)

Query `lumen_state.py current` before acting on any node; never trust dated prose.

## Paired restore
(added ≤2026-08-13; exercised wq#917 litellm restart)

Any automation that stops a service must carry a paired restore; confirm restored
before session end.

## Idempotency guard
(added ≤2026-08-13)

Replay/backfill jobs must be idempotency-guarded before first run.

## tag_sections is a singleton
(added ≤2026-08-13; live: /home/lumen/minerva/context/tag_sections.py — imported by path
in lumen_prose_lifecycle.py, referenced verbatim in lumen_federate.py; path verified wq#961)

Never run two tag_sections concurrently.

## Ingest nodes at provision time (inverse-apep)
(added ≤2026-08-13)

Ingest new nodes into the state store at provision time.

## Health checks read error lines
(added ≤2026-08-13; exercised wq#664 RCA)

Health checks must read error lines, not just unit state; oneshot 'inactive' after
clean exit is normal.

## Validate Gatus config before reload
(added ≤2026-08-13; it panics on bad config)

Validate Gatus config before reload.

## fworker spawn args
(added ≤2026-08-13; A7 §4.1 #26: verified true at fworker.py:1458, and flagged
"this is a lint, not an instinct" — router.py should emit both args or refuse)

FWORKER SPAWN: two mandatory args the router.py dispatch-cmd does NOT set correctly on
its own — (1) `--repo` must point at the actual git repo for the lane, not /home/lumen
(foreman-tok tickets → /home/lumen/foreman, questfree/questfree-root →
/home/lumen/questfree, lpp → /home/lumen/pokemon, etc.); (2) `--claude-bin
/home/lumen/.npm-global/bin/claude` MUST be passed because systemd unit PATH does not
include npm-global. Skipping either causes silent crash within 1s of spawn
(git-worktree fail then FileNotFoundError: claude). Verify with `systemctl list-units
lumen-job-fworker-wq*` at 10s post-spawn and confirm status=claimed on the ticket.
