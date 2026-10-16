# wq#4066 — verify-pass on wq#4062 ([selfheal] fleet node neith offline on tailscale for 30 min)

PR: `claude-foreman#2014`, merge commit `d082f96e2d` on main (current HEAD
`fc19ca85` is a descendant, two unrelated wq#4064 commits later), 1 file /
8 insertions / 0 deletions: `ops/wq3354-fleet-selfheal/selfheal.yaml`.
Author: claude-sonnet-5 (fworker-wq4062) · Verifier: claude-sonnet-5@high
(fresh instance, wq#4066).

## Claim under test

neith is an in-rotation Commander mcpw pool backend (SECOND STANDBY behind
horus), not a node meant to be permanently off and not a self-idle sleeper,
so a 30-minute tailnet dropout is a genuine page. The PR adds a single
`nodes.notes.neith` entry to `selfheal.yaml` carrying the 2026-10-07
SERVING/pool-failover observation (which supersedes an earlier 2026-10-05
not-reachable reading) and deliberately leaves `nodes.ignore` / `nodes.sleepers`
untouched so the page keeps firing.

## What I checked

1. **Read the diff cold** (`git diff d082f96e2d~1 d082f96e2d -- ops/wq3354-fleet-selfheal/selfheal.yaml`).
   Single hunk, inserted between `hathor` and `nephthys` — correct alphabetical
   position for a sorted `nodes.notes` dict. No other section touched;
   confirmed the `leakwatch.rules` block (same file, line 130+) is
   byte-identical before/after.

2. **Validated the YAML.** `python3 -I -c "import yaml; yaml.safe_load(...)"` —
   parses cleanly. `nodes.notes['neith']` is one continuous folded string (no
   stray newlines mid-word). `neith` is absent from both `nodes.ignore`
   (`['thor','maksoor','apis','pbox','seshat','be3600','3dprinter']`) and
   `nodes.sleepers` (`['hathor']`) — matches the claim exactly.

3. **Traced how `nodes.notes` is consumed** (`sh_nodes.py:185-187`): the note
   is only folded into the ticket body as `known issue: {note}`. It has no
   effect on the `ignore`/`sleepers` gates above it, so this diff structurally
   cannot suppress future paging — same proof shape as wq#4065's verify of
   the sibling ptah ticket.

4. **Checked the two specific factual claims against primary sources, not
   just against the ticket's own restated text:**
   - *"mcpw pool position 2 behind horus"* — `research/fleet_hardware.json`
     (committed 2026-10-12, so postdates and supersedes the older
     2026-10-05/06 role-assignment doc that still had neith=PRIMARY): `horus`
     role = `"Commander PRIMARY (MCP gateway)"`, `neith` role =
     `"Commander SECOND STANDBY (pool position 2 behind horus)"`, `khnum`
     role = `"Commander COLD STANDBY"`. Exact match.
   - *"status=SERVING observed 2026-10-07 01:56Z ... failover off khnum,
     supersedes 2026-10-05 not-reachable"* — ran `lumen_state.py current
     neith` and `current horus` directly (no SSH needed, already on-node).
     `neith.status` = `"SERVING — observed 2026-10-07 01:56Z: Commander MCP
     call landed on neith when the pool failed over from khnum ... Supersedes
     the 2026-10-05 not-reachable text."` — word-for-word match, not a
     paraphrase drift. `horus.status` independently corroborates from the
     other side: `"SERVING — Commander calls via mcpw land on horus (verified
     2026-10-07 05:47Z...); neith takes over when horus is down"`.

5. **Tried to break it.**
   - Is the "pending deploy" qualifier on `neith.role` (`"... in mcpw commit
     6e19641, pending deploy"`) evidence the horus-primary topology isn't
     actually live, undercutting the PR's premise? No — `horus.dc_redaction_gate`
     (dated 2026-10-14, two days after the fleet_hardware.json snapshot) shows
     horus's dc-gateway live and restarted with patches, and explicitly calls
     neith/khnum "standbys" reporting stale. horus is live; "pending deploy"
     reads as the specific mcpw pool-ordering code change, not the whole
     primary/standby arrangement.
   - Is the PR overstating confidence the source data doesn't have? Both
     `neith.status` and `horus.status` carry the same `[conf 0.41]` and
     `[UNVERIFIABLE — no probe]` tags — that's lumen_state's generic
     confidence-decay boilerplate applied fleet-wide to any fact without a
     fresh live probe, not a neith-specific red flag (horus's symmetric
     SERVING claim decays identically).
   - One real, narrow gap: `neith.status`'s source text ends with "Exact
     tunnel/mcpw backend path NOT inspected; verify before relying on it" —
     a content-specific hedge, not boilerplate. Both the wq#4062 ticket's
     auto-filed detail (truncated with an ellipsis) and the PR's note drop
     this hedge, stating the SERVING/in-rotation claim with more certainty
     than the primary source expresses. This doesn't change the operational
     decision (routing-path uncertainty argues for keeping the page live, if
     anything, not for suppressing it), so it's a writeup-precision nit, not
     a functional defect.
   - Should a first occurrence on neith also spawn an investigation/physical
     ticket, the way nephthys/ptah's repeat occurrences did? No — those
     tickets earned a dedicated physical ticket only after a pattern was
     established (nephthys: 3+ occurrences before tying to the AER alarm;
     ptah: wq#3957 first, wq#3949 physical ticket added only once unresolved).
     A single 30-minute blip with no established recurrence is correctly
     handled as "note + real page", matching this repo's own precedent.
   - Ran `pytest tests/` in the selfheal ops dir: 2 pre-existing failures in
     `test_shipped_rules.py` (leakwatch `.env` chmod rule count), confirmed
     via `git show d082f96e2d~1:...selfheal.yaml` vs current HEAD that the
     `leakwatch.rules` block is unchanged across this diff — unrelated to
     wq#4062, not a regression it introduced.

No contradiction found. The diff is minimal and scoped to exactly the line it
claims to touch, cannot alter paging/suppression behavior, and both
numeric/positional claims (pool position, SERVING timestamp, failover source)
verify against primary sources (`fleet_hardware.json`, live `lumen_state`)
rather than merely matching the ticket's own auto-generated text.

## Verdict

**CONFIRM.** wq#4062's fix does what it claims: neith is correctly classified
as a live in-rotation Commander backend via `nodes.notes` (not
`ignore`/`sleepers`), and every checkable factual claim in the added note
holds up against `research/fleet_hardware.json` and live `lumen_state` data.
Minor nit (not a contradiction): the note drops `neith.status`'s own
"exact backend path NOT inspected" hedge — worth carrying forward if this
entry is touched again, but it doesn't affect the classification decision.
