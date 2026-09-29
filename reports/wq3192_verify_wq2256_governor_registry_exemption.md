# wq#3192 — verify wq#2256 [C8/wq2241] retire the frozen-prefix naming rule: point the governor at the registry

Archetype 8 adversarial verify pass. Author: claude-opus-5-5. Verifier: claude-sonnet-5@high
(fresh instance).

Commit: `670d6744` on main, "feat(governor): derive the local-pool exemption from models.yaml"
(PR #1454, merged). Design doc: `research/WQ2256_GOVERNOR_REGISTRY_DESIGN.md`. Files touched:
`governor/governor_core.py`, `harness/inference_registry.py`, `harness/node_lifecycle.py`,
`harness/node_render.py`, `harness/tests/fixtures/wq2256_models_fourth_family.yaml`,
`harness/tests/test_inference_registry.py`, `harness/tests/test_prefix_list_parity.py`.

## Claim under test

The governor's local-pool exemption was a frozen five-item tuple, so a new local model family
needed a governor source edit before it could dispatch without tripping Anthropic quota floors.
wq#2256 makes the exempt set the union of that frozen tuple and the `models.yaml` rows whose
`pools` include `local`/`cheap_cloud` (DARK rows skipped, Anthropic ids never exempt regardless
of pool). Acceptance: T4 liveness suite passes; a fourth-family fixture id matching no frozen
prefix lints clean and is governor-exempt; a `claude-*` id and a `pools: [cloud]` row stay
non-exempt; an unreadable `models.yaml` falls back to exactly the frozen tuple.

## What I checked

- Read the diff cold (`git show 670d6744`) and the design doc end to end before looking at the
  tests, to form my own model of the intended behavior first.
- Ran the ticket's own liveness command:
  `python3 -m pytest harness/tests/test_prefix_list_parity.py governor/test_governor_core.py -q`
  → **72 passed**.
- Read every added/changed assertion in `test_prefix_list_parity.py` (lines 143-209): fourth-family
  exemption (`test_fourth_family_matching_no_frozen_prefix_is_governor_exempt`,
  `test_fourth_family_lints_clean_against_its_models_yaml`), claude/cloud non-exemption
  (`test_claude_and_cloud_rows_stay_non_exempt`, `test_claude_family_in_the_registry_fails_lint`),
  and the fallback (`test_unreadable_models_yaml_falls_back_to_the_frozen_tuple`, parametrized over
  a missing file, unparsable YAML, and a non-dict top level). All match the stated acceptance
  criteria exactly, not a weakened version of them.
- Ran `harness/tests/test_inference_registry.py` standalone: 25 passed, 1 failed
  (`test_seeded_rows_reproduce_measured_table`, nephthys `decode_tps` 4.79 vs pinned `None`). This
  is the pre-existing, already-logged ISSUE-LOG #1082 ("not in wq#2256 scope (data, not
  governor)") — confirmed unrelated to this diff (a `research/inference_nodes.yaml` data drift,
  not governor/registry code), matching wq#2256's own done-notes.
- Read `harness/node_lifecycle.py:210-212` (`_governor()`, used by `fleet-node add`'s precheck) and
  `harness/node_render.py:1-40` (`PREFIX_FILES`, the "governor is never rendered into" claim) to
  confirm the four editable `LOCAL_POOL_MODELS` copies stay hand-rendered literal tuples and the
  governor is deliberately excluded from that render set — matches design §5.
- **Falsifiability / adversarial probing beyond the shipped fixture.** Wrote scratch scripts
  (`/tmp/fworker-wq3192/probe1.py`, `probe2.py`) importing `governor_core` directly against
  throwaway `models.yaml` files, not the repo's fixture, to look for a case the shipped tests
  don't cover:
  - Confirmed `_registry_local_ids()` correctly excludes ids containing `claude`/`anthropic`/`fable`
    as an exact design match, and confirmed the frozen tuple is a floor (union, never shrinks)
    against a scratch file that removes matching rows entirely.
  - Found a real gap outside the ticket's stated acceptance criteria: `_registry_local_ids()`
    (`governor/governor_core.py:120-124`) does
    `any(p in (m.get("pools") or []) for p in _EXEMPT_POOLS)`. When a row's `pools` field is a bare
    YAML scalar string instead of a list (e.g. `pools: not-local-pool` — a plausible typo, dropped
    brackets), Python's `in` becomes substring containment instead of list membership: any pools
    value containing the literal text `local` or `cheap_cloud` anywhere in it grants full
    Anthropic-quota governor exemption. Reproduced live: `local_pool_models(path)` included the
    scalar-pools id and `is_local_model()` returned `True` for it (see probe2.py output). No schema
    check anywhere in the repo enforces `pools:` as a list before this function reads it. This
    pattern is copied from the preexisting `harness/concurrency.py::_pool_models` (same `in` idiom,
    not touched by this diff), so it is not new to wq#2256, but wq#2256 is the diff that extends it
    into the security-sensitive path the design doc's own "Risk" section calls out ("The governor
    gates Anthropic burn"). Logged as ISSUE-LOG #1085 (open, not fixed — verify-pass scope is
    verify, not re-implement). Does not falsify any of the ticket's stated acceptance criteria:
    every row in the real `research/models.yaml` already uses correct list syntax, and the ticket
    never claimed to add `pools` schema validation.
- Re-read `harness/inference_registry.py::governor_exempt()` (loads `governor_core.py` by path
  under a private module name, optionally rebuilding `LOCAL_POOL_MODELS` against a caller-supplied
  `models_yaml`) to confirm the lint path and `node_lifecycle._governor()` both exercise the real
  governor logic against the registry's own `models.yaml`, not a copy — so a fourth-family
  `fleet-node add` precheck and `fleet-node lint` genuinely ask the shipped predicate, not a stand-in.

## Verdict: CONFIRM

wq#2256 does what it claims. The liveness suite passes (72/72), every stated acceptance criterion
(fourth-family exemption, claude/cloud non-exemption, unreadable-yaml fallback, four editable
copies unchanged, governor never rendered into) is independently verified against the real code
paths, not just the green test suite. The one adjacent bug my own probing surfaced (`pools` as a
bare string producing exemption-by-substring) is real but sits outside the ticket's stated scope,
predates this diff in a sibling module, and does not contradict anything wq#2256 asserted — logged
to ISSUE-LOG #1085 as a follow-up rather than treated as a contradiction. No escalation to
opus-5@high.
