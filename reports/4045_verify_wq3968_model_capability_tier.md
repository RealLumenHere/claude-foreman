# wq#4045 — verify wq#3968: LIMPET model-capability tier (RE step routing)

**Verdict: CONFIRM.** No defects found; acceptance criteria met; shipped as described.

## What was verified

PR #1980 (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1980), merged to
main as `e0241156`. Diff: `limpet/manifest.py` (+42), new `limpet/model_router.py`
(+60), `limpet/test_limpet_manifest.py` (+31), new `limpet/test_model_router.py`
(+67), `design/wq3929_competitive_analysis.md` (+12 build note). Commit already
reachable on the assigned worktree branch (`feat/wq4045-...`), so the diff was
read directly from git history rather than refetched from Gitea (API returned
404 for this private repo unauthenticated — expected, not an incident per
instinct #8; nothing in the diff depended on reaching the API).

Ticket wq#3968 ACCEPT clause: "manifest schema field + validation test, router
unit test showing specialized route when configured and fallback when not."
Both present: `manifest.MODEL_CAPABILITIES` + `[model_routes]` table +
`_validate_model_routes` (fail-closed on bad shape/typo'd capability), and
`model_router.resolve_model`/`resolve_step` with tests for the configured,
fallback, "general"-always-wins, and unknown-capability-fails-closed cases.

## Adversarial checks performed

1. **Read both new modules cold, line by line.** `resolve_model`/`resolve_step`
   logic is correct: "general" always returns `resident_model` regardless of a
   stray `capability_models["general"]` entry (tested); any other capability
   not in `MODEL_CAPABILITIES` raises `RoutingError` (closed taxonomy, fails
   closed); an undeclared step in a manifest's `model_routes` defaults to
   "general" rather than silently inheriting a specialized route.
2. **Ran the new/changed tests directly:**
   `python3 -m pytest limpet/test_model_router.py limpet/test_limpet_manifest.py -q`
   → 43 passed.
3. **Ran the full limpet suite** (`python3 -m pytest limpet -q`, excluding the
   4 pre-existing collection errors) → 160 passed, no regressions. The 4
   excluded files (`test_limpet_cli.py`, `test_limpet_daemon.py`,
   `test_limpet_registry.py`, `registry/tests/test_registry.py`) fail to
   collect with `ImportError: cannot import name 'Registry' from 'registry'`
   — confirmed pre-existing (ISSUE-LOG rows #1314/#1333/#1334, a `registry.py`
   vs `registry/` package shadowing bug from wq#3807/wq#3810, untouched by
   this diff's file list). The PR's own done-notes cite the same rows and the
   same 160/160 count; verified accurate, not fabricated.
4. **Checked every non-obvious citation in the new docstrings/comments against
   the actual files** (instinct #8 — a fabricated citation reads as a found
   problem only if you don't check it):
   - `design/wq3806_limpet_architecture.md:260-266` (cited in both
     `manifest.py` and `model_router.py` for the "32GB/iGPU ceiling" / "route
     hard steps via LiteLLM" claim) — confirmed, matches verbatim. (Note: the
     original wq#3968 ticket text cited `:233-239` for the same idea — that
     span is now "Workspace isolation" in the current doc. The builder used
     the live, correct line numbers rather than the ticket's stale citation;
     not a defect.)
   - `docs/settings-spec.md` §4.1 `llm.routing.<step>` not wired to a real
     client yet — confirmed (§4.1 defines the key; a later TODO says wiring to
     LiteLLM's `/v1/models` is still pending).
   - wq#3995 ReCopilot China-hosted-API egress finding, referenced as the
     reason ReCopilot itself isn't a route target — confirmed in
     `design/wq3929_competitive_analysis.md` (5), which the diff's own build
     note appends to.
   - `limpet/examples/llm4decompile.toml` (wq#3884) cited as the verified
     open-weight alternative — file exists, content (MIT+DeepSeek, GGUF
     1.3B–33B) matches the claim. It does not itself declare `[model_routes]`
     (not required by this ticket — no agent-core caller exists yet to
     consume it; correctly scoped as design-only, matching the `critic.py`
     posture this module explicitly mirrors).
5. **Checked for integration breakage**: `PluginManifest(...)` is
   constructed in exactly one place (`manifest.py:333`, `manifest_from_dict`),
   so the new `model_routes` field (default-factory `dict`) can't desync any
   other caller — grepped the repo, none exist yet.
6. **Tried to break the validator**: non-dict `model_routes`, typo'd
   capability, capability not in the closed list — all rejected with the
   expected error text already covered by tests; traced through
   `_validate_model_routes` manually, it fails closed in every bad-shape case.

## Why this doesn't escalate

No contradiction found. The implementation matches its stated scope (a pure,
offline-testable routing *decision*, no model client — deliberately, same as
`critic.py`), the acceptance criteria are met with passing tests, and every
checkable citation in the new comments resolves to real content at the stated
location. Nothing here needs opus-5@high.

## Evidence trail

- `git show e0241156` — full diff reviewed.
- `python3 -m pytest limpet/test_model_router.py limpet/test_limpet_manifest.py -q` → `43 passed`.
- `python3 -m pytest limpet -q --ignore=registry/tests/test_registry.py --ignore=test_limpet_cli.py --ignore=test_limpet_daemon.py --ignore=test_limpet_registry.py` → `160 passed`.
- `/home/lumen/kb/ISSUE-LOG.md` rows #1314, #1325, #1332, #1333, #1334 cross-checked against the PR's done-notes and this diff's build note.
