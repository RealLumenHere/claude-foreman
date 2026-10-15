# wq#3979 — verify-pass of wq#3945 (LIMPET plugin system refinement)

PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1924 (merged `f272e5117d`)
Author model: claude-sonnet-5. Verifier: claude-sonnet-5@high (fresh instance).
Verdict: **CONFIRM** — no contradiction, no escalation to opus-5@high.

## Scope read cold

PR is 2 commits (`d3239f9` + `f272e51`) on top of `628e39b0`. Diff: `docs/plugin-spec.md`
(new, 202 lines), `design/wq3810_limpet_plugin_system.md`, `limpet/README.md`,
`limpet/cli.py`, `limpet/manifest.py`, `limpet/registry.py`, `limpet/registry_core.py`,
4 example `.toml` files, and test additions across `test_limpet_{cli,manifest,
registry,registry_core}.py`. Net: +529/-35 across 15 files.

Adds: a closed `category` taxonomy (10 values) for `limpet list --category`; a `data`
plugin kind; `[optional_dependencies]` (soft deps, never block, per-entry
`optional_dependencies_satisfied` reported); `conflicts` (mutual-exclusion, declared
one-sided, mirrored symmetrically by `_symmetric_conflicts`, enforced only against an
already-`active` conflicting plugin).

## What I ran

1. `python3 -m pytest limpet/ -q` (documented entrypoint, `limpet/README.md:52`) —
   reproduces the pre-existing, already-logged (ISSUE-LOG #1334) `registry.py` /
   `registry/` package name collision (`ImportError: cannot import name 'Registry'`).
   Confirmed by `git cat-file -e 628e39b0:limpet/registry/__init__.py` that this
   collision predates wq#3945's commits — out of this PR's scope, not a regression.
2. Worked around the collision the same way ISSUE-LOG #1334 and
   `limpet/tools/rockchip/test_rockchip_mcp.py:_load_by_path` do: load
   `limpet/registry.py` by explicit file path under the canonical `registry` name in
   `sys.modules` before collection, then ran the full suite minus the unrelated
   `limpet/registry/tests/` subpackage (that one fails on `ModuleNotFoundError: No
   module named 'limpet'` — a packaging-path issue in a different, untouched
   subsystem, also not this PR's scope). Result: **146 passed, 0 failed**.
3. Three adversarial probes against `registry_core.resolve()` outside the tests the
   PR shipped (archetype-8 "try to break it"), not just rerunning its own tests:
   - Conflict target is `blocked` (unmet dependency), not `disabled` — the only state
     the shipped tests cover. Result: the plugin with the conflict stays `active`
     (correct: conflict enforcement is keyed on `state == "active"`, so only an
     active opponent blocks).
   - Three-way mutual conflict ring (A↔B↔C↔A), never tested. Result: exactly one
     node ends `active`, the other two `blocked` — matches the doc's invariant
     ("the registry keeps exactly one active") for an arbitrary conflict graph, not
     just the pairwise case the tests exercise.
   - Self-referential `optional_dependencies` entry (not validated against, not
     tested). Result: resolves to `True` trivially, no crash — harmless since
     optional deps are advisory-only.
   All three ran clean; no counterexample found.
4. Read `_symmetric_conflicts` (`limpet/registry_core.py:84-94`) closely because it
   mutates a dict value (a `set`) while iterating a `list()` snapshot of the same
   dict's items — looked suspicious at first (the snapshot holds live set references,
   so a later iteration can see an already-mutated set). Hand-traced several
   topologies (path A–B–C, a fan-in, out-of-order declaration) and confirmed by
   construction that every mutation only ever re-applies a pairwise edge that one
   side already declared; it never synthesizes a transitive edge between unrelated
   nodes. Redundant work, not a correctness bug.

## Spec/doc cross-check

`docs/plugin-spec.md` §9's shipped/future-work table claims category, conflicts,
`data` kind, and soft deps as shipped, and central index/signing/sandboxing as not
shipped. Checked each shipped claim against the actual code (manifest.py validation,
registry_core.py resolution, cli.py `--category`) — matches; no overclaiming found.
The `hackrf.toml` / `hackrf-portapack.toml` worked example for `conflicts` and
`ghidra-mcp.toml` for `optional_dependencies` are real, valid, cited-by-line-number
in the docs, and parse/validate cleanly.

## Minor note (not a contradiction)

`limpet list --category X --json` filters the `plugins` key but leaves `start_order`
unfiltered (still lists all active plugins, including ones outside the category).
Harmless for a discovery/browse view and not addressed by the PR's own tests either
way — flagging for awareness, not disputing the merge.

## Conclusion

wq#3945's diff is correctly scoped, its new resolution logic holds under probes
outside its own test population, its tests pass (146/146 excluding two pre-existing,
already-logged, out-of-scope collisions), and its documentation matches the shipped
code. **CONFIRM.**
