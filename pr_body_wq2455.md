# prior-art doctrine reaches local probe-build dispatch

## What

The prior-art search doctrine now reaches probe-build tickets dispatched on the local rung,
which previously never saw it.

- `harness/prompts/local_probe-build.md`: a ~45-token PRIOR ART FIRST bullet, placed ABOVE
  the `---` separator so it lands in the byte-identical static head the backend's KV prefix
  cache reuses. The rendered prompt is 733 estimated tokens against the 4000 gate.
- `harness/fworker.py:2707`: a comment at the branch recording that worker.md's doctrine
  does not arrive by inheritance on this path, that `local_mechanical-edit.md` omits the
  bullet on purpose (a named mechanical edit is out of reverse-engineering scope), and that
  adding a third local archetype means deciding this again.
- `harness/tests/test_charging.py`:
  `test_local_probe_build_carries_the_prior_art_doctrine_in_the_cached_prefix` pins
  fleet-search into the cached prefix, so a later edit cannot drop it or push it below the
  separator silently.

## Why

wq#2452 (fresh sonnet-5@high verify pass, re-confirmed by opus-5@high) contradicted
wq#2439's claim that its `harness/prompts/worker.md` DOCTRINE addition "reaches every future
ticket." It does not. A local-rung dispatch of a local-trusted archetype never loads
worker.md:

- `local_prompt_template()` (harness/fworker.py:2695-2704) returns
  `harness/prompts/local_{archetype}.md` whenever `not charging.legacy_mode()`
  (harness/charging.py:85-86 — false by default, so this is the DEFAULT path) and
  `is_local_rung(model)`.
- harness/fworker.py:3070-3076 branches around `load_worker_prompt()` entirely.
- Neither slim local template mentioned fleet-search. `load_local_prompt()` also skips
  `{{INSTINCTS_TIER0}}` for a stated 4k-token budget reason (harness/fworker.py:2713), not
  because the doctrine does not apply.

probe-build's own KIND line — "probe and build. Investigate the named question… then make
the one small change or script the ticket asks for" — is exactly the reverse-engineering /
tool-build / "how do we solve X" shape the doctrine targets, and its primary tuple is
`local-worker@local` by design (harness/agents.yaml:56-60), so this is the default path
rather than an edge case.

## How

Restate the doctrine, do not re-route the archetype.

Dropping probe-build from `LOCAL_ALLOWED_ARCHETYPES` was rejected: it would undo a
deliberate routing decision, and the local rung's main reason to exist, to fix what is a
prompt-text gap. Narrowing the claim alone was rejected because it leaves the gap open on a
default-on path.

The bullet is actionable as written: Bash is available to local workers (`LOCAL_CLI_TOOLS`,
harness/fworker.py:1918) and `Bash(/home/lumen/bin/fleet-search *)` is allow-listed in the
same `harness/worker-settings.json` that both dispatch paths resolve through
(harness/fworker.py:3142). Only the instruction to search first was missing.

The correct scope of the earlier claim is "every worker.md dispatch", plus probe-build's
local template. Recorded as ISSUE-LOG row 639.

## Testing

`python3 -m pytest harness/tests/test_charging.py harness/tests/test_local_rung.py -q`
→ 66 passed in 81.48s. That run includes the pre-existing
`test_local_prompt_static_prefix_is_byte_identical_across_tickets` and
`test_local_prompt_is_slim_and_fits_the_default_ctx_gate`, both parametrized over
mechanical-edit and probe-build, so the prefix-cache and 4k-token guarantees are covered
after the edit.

## Risk / rollback

Low. The change is three lines of prompt text plus a comment and a test; no control flow
moves. The one real cost is a single KV prefix-cache miss per backend the first time a
probe-build ticket renders the new head, after which the head is byte-identical again.
mechanical-edit's prompt is untouched, so the higher-traffic local archetype is unaffected.
Rollback is `git revert` of this commit.

Refs: wq#2455, wq#2452

🤖 Generated with [Claude Code](https://claude.com/claude-code)
