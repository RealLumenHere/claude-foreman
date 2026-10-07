# wq#3620 — verify-pass of wq#3619 (PR #1707)

**Author:** claude-sonnet-5 (standard-engineering) · **Verifier:** claude-sonnet-5@high (fresh instance)
**PR:** http://100.121.17.5:3000/lumen/claude-foreman/pulls/1707 — merged as `ae3a3c89`, landed on `main` as `2a6c6bae` (rebase, content identical: `git diff ae3a3c89 2a6c6bae -- ops/wq3354-fleet-selfheal/` is empty).

## What shipped
`ops/wq3354-fleet-selfheal/selfheal.yaml` gains a `nodes.notes` map (keyed by lumen_state
node name) and `sh_nodes.py:124-127` folds a matching note into a node-offline ticket's
detail, alongside the existing power-state lines. One node configured: `nephthys`, with a
note describing its recurring AER-storm-correlated power loss. One new test added:
`test_node_offline_ticket_folds_in_configured_known_issue_note` (uses a synthetic `isis`
fixture, not `nephthys`, to stay independent of the real config).

Diff is exactly the 3 files / 24 insertions / 1 deletion the PR claims — no scope creep.

## Verification performed
- **Code read cold** (`sh_nodes.py` sweep/power_state/node_for in full): `lines` is a
  fresh list per call from `power_state`, so the `.append(known issue...)` cannot leak
  across nodes or sweeps; `note = ncfg.get("notes", {}).get(node)` is a plain per-node
  dict lookup, exact key match, so it can't apply to the wrong node or survive a missing
  `notes:` key (falls back to `{}`).
- **YAML parses correctly**: `yaml.safe_load`'d `selfheal.yaml` confirms `nodes.notes ==
  {"nephthys": "<the note>"}` and `nodes.ignore` is unchanged (7 entries, no `nephthys`) —
  the note is additive context, not a page-silencer, exactly as its own inline comment
  claims ("NOT a silencer like ignore above, the page still fires").
- **Tests**: ran the full class-6 suite fresh, not trusted from the PR notes —
  `python3 -m pytest ops/wq3354-fleet-selfheal/ -q` → **57 passed**, matching the author's
  claimed "57 of 57". The new test asserts the folded string appears in ticket `detail`.
- **Adversarial fact-check of the note text itself** (an acceptance-probe doctrine
  concern: a known-issue note that's wrong is worse than no note). Every factual claim in
  the `nephthys` note was checked against the actual code it references, not taken on
  faith:
  - `aer-storm:nephthys` alarm key — real, `ops/wq2851-aer-storm-alarm/aer_storm_alarm.py:113/126` builds exactly `f"aer-storm:{node}"`.
  - `power-remediate is deliberately ARMED=0` — real, `/home/lumen/bin/power-remediate:29,328,411` confirms `ARMED=0` is the documented default, observe/log-only.
  - `ELIGIBLE` set / wq#2776 / wq#2851 cross-refs — real, `ops/wq2851-aer-storm-alarm/README.md:4,14,18` and `aer_storm_alarm.py:9-11` corroborate nephthys's inclusion and the 3x-drop history.
  - `do NOT add nephthys to nodes.ignore` — correct per the diff itself; nephthys is in `notes`, not `ignore`.
- **Break attempts that didn't break it**: checked for cross-node note bleed (none,
  per-node dict lookup), checked the first-sweep baseline path also includes the note
  (it does — `due` is built once and reused by both the baseline and steady-state filer),
  checked backward compat when `notes:` key is absent (16 other node-offline/non-node
  tests in the file don't set it and still pass).

## Verdict: CONFIRM

No discrepancy found between the PR's claims and the code/tests/config as they actually
stand on `main`. Scope matches, tests are real and green, and the note's own factual
content — the part most likely to rot or be fabricated — is independently corroborated by
other files in the repo. Not escalating to opus-5@high; no contradiction to escalate.
