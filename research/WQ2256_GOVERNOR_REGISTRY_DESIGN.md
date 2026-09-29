# wq#2256 — retire the frozen-prefix naming rule: design

Status: design only. Frank lifted the governor-edit prohibition on 2026-09-29 (queue directive
"repoint governor"). The ticket's original 3-line diff (`from concurrency import LOCAL_POOL_MODELS`
at `governor/governor_core.py:97`) does not work as written, for the reasons below.

## Why the 3-line diff breaks

1. `governor_core` is imported as a bare module (`foreman_usaged.py` puts only `governor/` on
   `sys.path`), so `harness.concurrency` is not importable from it.
2. `governor_core` is deliberately dependency-free at import time (stdlib + `from __future__`); a
   new import there can fail the whole governor at load.
3. `concurrency.LOCAL_POOL_MODELS` holds full model ids (`qwen36-local`, `hathor-llama8b`), while the
   governor and its four siblings use them as `startswith` prefixes for `<family>.<node>` ids and
   `@effort` suffixes. Swapping one for the other changes matching semantics, not just the source.
4. `harness/tests/test_prefix_list_parity.py` pins exactly five literal sites
   (`test_exactly_five_files_define_the_literal`) and `test_fworker_and_governor_copies_equal_registry_render`
   compares the governor tuple to `governor_prefixes`.
5. `inference_registry.py` lints against the same prefixes (`_lint_ids` lines 160-170, tuple
   comparison at 275-287), so relaxing only the governor leaves `fleet-node add` refusing a fourth
   family.

## Design

The governor derives its exempt set from the registry instead of a frozen tuple, with the frozen tuple
kept as the fallback. The registry rows in `models.yaml` are already family-level ids (`qwen36-local`,
`hathor-llama8b`, `local-worker`); node and effort suffixes are appended at runtime. So the full row id
is the prefix, with no attempt to cut an id down to a "family" (that would misfire on ids that contain
a `.` such as a versioned `gemini-3.1-flash`).

1. `governor/governor_core.py` (stdlib only): a local `_registry_local_ids()` reads
   `research/models.yaml` (path relative to the file, `FOREMAN_MODELS_YAML` override, same as
   `concurrency.py:51`), takes ids of rows whose `pools` include `local` or `cheap_cloud`, and skips
   `status: DARK`. `yaml` is imported inside the `try`. `LOCAL_POOL_MODELS` is the union of that set
   and `_FROZEN_LOCAL_POOL`. `_FROZEN_LOCAL_POOL` is the
   current five-item tuple and is also the floor: the result is the union of the two, so an
   unreadable or emptied registry can only shrink to today's behaviour, never below it.
2. `is_local_model` is unchanged (`startswith` over the tuple), so `qwen36-local.isis@low` and
   `gemma3-27b.isis@low` both stay exempt.
3. `inference_registry.py`: drop the `governor_prefixes` equality check and the `governor-prefix`
   id lint. Replace with one check: every registry model id is exempt under
   `governor_core.is_local_model` (import the module by path, as `governor_prefixes()` already does).
   A fourth family then lints clean, and a cloud id (`claude-*`) still fails.
4. Tests: `test_exactly_five_files_define_the_literal` becomes "four editable literals plus one
   derived site"; the governor case in `test_fworker_and_governor_copies_equal_registry_render`
   compares the derived set to the registry ids; add a fourth-family fixture id that matches no frozen
   prefix and assert it lints clean and is governor-exempt (the ticket's acceptance); add a negative
   test that a `pools: [cloud]` row is not exempt and that an unreadable models.yaml falls back to
   the frozen tuple.
5. Leave the four editable copies alone. They stay rendered from the registry's `governor_prefixes`
   field. Open point for the implementer: that field is hand-listed in `research/inference_nodes.yaml`,
   so either generate it from the same `models.yaml` pools or keep it and let the new lint in step 3
   be the check that catches drift.

## Risk

The governor gates Anthropic burn. Before: only a source edit could exempt a model. After: a
`models.yaml` row with `pools: [local]` exempts it. `models.yaml` is repo-tracked and PR-gated, and
`claude-*` ids are explicitly tested as non-exempt, so the exposure is one reviewed line. Rollback is
reverting the governor commit; the frozen tuple stays in place as the fallback.

## Verify

`python3 -m pytest harness/tests/test_prefix_list_parity.py governor/test_governor_core.py -q`, plus
`fleet-node lint` against a scratch registry containing a fourth-family id.
