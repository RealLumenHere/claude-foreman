# wq#3909 — verify wq#3904: LIMPET settings/preferences panel design spec

Archetype 8 (adversarial verify-pass) of PR
http://100.121.17.5:3000/lumen/claude-foreman/pulls/1882 (merged 6bafaf7eed into
9dd218c7, `docs/settings-spec.md`, 293 lines). Author: claude-sonnet-5. Verifier:
claude-sonnet-5@high, fresh instance, same model as author.

## What this ticket is

`docs/settings-spec.md` is design-only prose: no code, no config file, no package.
There is no unit test or CLI probe to run. The adversarial probe available for a
design doc is citation verification — every `file:line` and quoted string the doc
relies on is a falsifiable claim about the state of this repo, pinned to the
worktree's checked-out commit (immutable, re-runnable). A wrong citation, a
misquote, or a logical contradiction between this doc and the docs it claims to
build on would be a real finding (cf. instinct 8 — a miscite is evidence of the
kind this verify pass exists to catch).

## Method

Read `docs/settings-spec.md` cold, then checked every citation against the actual
file/line it names:

- `design/wq3806_limpet_architecture.md` — lines 3, 71-88, 137-141, 183-205,
  212, 216, 224-226, 228-249 (trust tiers, HAL `list_hardware`, Coral accelerator,
  workspace layout/containment, 9B-vs-14B memory-ceiling recommendation,
  promotion-is-a-review-step).
- `design/wq3810_limpet_plugin_system.md` — lines 44-50, 103-108 (fail-closed
  validation, atomic write, stdlib-only `tomllib`).
- `docs/desktop-spec.md` — lines 270-290, 345-352 (the `limpet-settings.desktop`
  `Comment=` line, the "one entry, both scopes" contract, and §9's open items).
- `docs/agent-chat-spec.md` — lines 8-18, 95-115, 183-217 (headless constraint,
  Textual-vs-Tauri-vs-Electron framework table, LiteLLM `/v1/models` picker,
  local/fleet pool tagging).
- `limpet/registry.py` lines 10-16, 38-44 (`state_dir` layout, `_atomic_write_json`).
- `limpet/cli.py` lines 50-82, 118-152 (`_cmd_list`, `_cmd_toggle` wired to
  `enable`/`disable` subparsers, exit-code convention: 0 ok / 1 usage-or-not-found
  (`OSError` path) / 2 validation failure (`ManifestError` path) — confirmed by
  reading the actual `return` statements, not assumed from the doc's own claim).
- `research/fleet_hardware.json` lines 417-428 (Limpet row: `ram_gb: null`,
  `status: "unprovisioned-unfiled"`).
- `research/models.yaml` lines 18-22 (`local` / `cheap_cloud` pool taxonomy).
- `docs/target-wizard-spec.md` §4.5 (workspace creation step the settings'
  `workspace.*` defaults feed).

Every one of these checked out exactly as quoted or paraphrased — no fabricated
line number, no misquote, no claim about code behavior (exit codes, atomic-write
pattern, enable/disable wiring) that the actual source contradicted.

## One gap worth recording (does not change the verdict)

`docs/settings-spec.md` §0.1 and §5 pick Textual for `limpet-settings`, citing the
same framework reasoning as the chat interface. `docs/desktop-spec.md` §9 (lines
349-352) — a doc §0.1 itself quotes 60-odd lines earlier — left this exact
question open: *"Decide whether `limpet-settings` is a new small GUI or a
`yad`/`zenity` form over `limpet` + `xfconf-query` (cheaper; recommended for
v1)."* The settings-spec never mentions `yad`/`zenity`/`xfconf-query` anywhere
(`grep -i` across both docs confirms), and doesn't list closing this open item in
its own §8. It silently resolves it in the opposite direction from desktop-spec's
"recommended for v1" without saying so.

This is not a contradiction that breaks the design: `yad`/`zenity` need a running
X server/`$DISPLAY`, which cuts directly against the settings-spec's own §0.2
headless-first constraint ("fully usable with no display attached") — Textual's
plain-SSH-pty operation is the better-supported choice on the facts this doc
itself establishes. But the doc should have said so and closed the open item
explicitly rather than leaving a reader of both docs to notice the silent
override. Non-blocking; worth a one-line note if the doc is touched again.

## Verdict: CONFIRM

No fabricated citation, no broken cross-doc logic, no internal inconsistency in
the seven-domain scope, the CLI contract, or the security section. The one gap
above is a documentation-completeness nit with a ready justification already
present in the doc's own constraints, not a correctness defect. Board updated;
no escalation to opus-5@high.
