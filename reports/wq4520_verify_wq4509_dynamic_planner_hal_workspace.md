# wq#4520 verify-pass: wq#4509 Limpet dynamic planner (HAL inventory + workspace context)

**Target:** PR `claude-foreman#2374` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/2374),
commit `3b690076db5327b56b8c06aa000c6337708a3ed4` on `main` ("feat(wq4509): dynamic planner
with HAL inventory and workspace context"; wq#4509's own notes cite the merge hash as
`c90894d291`). Author: claude-sonnet-5 (fworker-wq4509). Verifier: claude-sonnet-5@high
(fresh instance, wq#4520).

**Verdict: CONTRADICT.**

## What the diff claims

wq#4509 adds `limpet/planner.py` (116 lines) + `limpet/test_planner.py` (163 lines, 9 tests).
`Planner.build_system_prompt()` assembles a base prompt plus three sections rebuilt fresh
per call: HAL device inventory (from `hardware.json`, liveness-gated on `hal.sock`
existing), the active workspace's `manifest.json` (target identity + hardware bound), and a
5-entry "warm band" of recent `evidence.jsonl` tool-call results. wq#4509's done-notes
disclose (citing ISSUE-LOG #1518) that no pre-existing `planner.py` or
`agent-processor-spec.md` was reachable from that sandbox, so the module was built fresh from
the ticket body rather than by filling in hooks in an existing file. The module's own
docstring states the design contract explicitly: "Each section degrades to a plain status
line instead of raising when its source is absent ... a planner that crashes because the box
has no USB devices attached is worse than one that says so in the prompt."

## What I checked

- Read `limpet/planner.py` and `limpet/test_planner.py` cold (no re-use of the author's own
  framing).
- Confirmed every module/function the diff imports is real and matches the signatures used:
  `ops/wq3809-limpet-hal/hal_daemon.py` (`_write_inventory` at line 87, writes
  `json.dump(list(self.inventory.values()), ...)` — a flat list of dicts, matching
  `_read_hal_inventory`'s expectation), `limpet/workspace_console.py`
  (`read_blocks`/`project`/`format_line`, lines 52-113), `limpet/workspace_report.py`
  (`mask_text`). Nothing fabricated.
- Confirmed the HAL liveness check (`os.path.exists(HAL_SOCKET)` as a stand-in for "daemon
  running") is not invented for this diff: `hal_daemon.py`'s own `status` subcommand
  (line 215) uses the identical pattern. Not a new assumption.
- Confirmed the one attacker-reachable field, the raw USB descriptor (`hal.py`'s `detect()`,
  `device.desc`), is masked via `wr.mask_text()` before being placed in the prompt
  (`planner.py:71`), and `test_hal_inventory_masks_credential_shaped_descriptor` exercises
  this. `name`/`usb_id`/`device_node`/`status` are plugin-static or numeric, not raw
  attacker text — correctly left unmasked.
- Ran the new tests: `python3 -m pytest limpet/test_planner.py -q` → **9 passed**.
- Ran the full `limpet/` suite (excluding `registry/` and `iso-builder/`, which have
  pre-existing, unrelated import-path issues when collected from inside `limpet/`):
  **600 passed**, confirming no regression from this diff.
- Checked integration: `grep -rn "build_system_prompt|Planner(" limpet/*.py` outside
  `planner.py`/`test_planner.py` → **zero hits**. Nothing in `agent_tui.py`, `model_router.py`,
  or any other limpet module calls `Planner.build_system_prompt()`. This matches the honestly
  disclosed sandbox gap (ISSUE-LOG #1518) rather than a false "wired into the agent loop"
  claim — the done-notes never assert integration — so this is not treated as a contradiction
  on its own, just confirmed scope.
- **Broke it** — adversarially fed `build_system_prompt()` manifest shapes the test suite
  never tries, by cross-referencing how *other* modules in this same codebase already handle
  the identical `manifest.json` fields:
  - `workspace_report.py:313` reads `identity` as
    `man.get("identity", {}) if isinstance(man.get("identity"), dict) else {}`.
  - `workspace_report.py:315` and `agent_tui.py:114` read `hardware_bound` as
    `... if isinstance(..., list) else []`.
  Both sibling modules guard these two fields against the wrong JSON type. `planner.py:99`
  (`identity = man.get("identity", {}) or {}`) and `planner.py:101`
  (`hw_bound = man.get("hardware_bound", []) or []`) do not — they assume the field, if
  present, is already the right container type.
  - Reproduced with a manifest where `identity` is valid JSON but a list, not a dict:
    `Planner(workspace=ws).build_system_prompt()` → `AttributeError: 'list' object has no
    attribute 'items'` (from `", ".join(f"{k}={v}" for k, v in identity.items())` at
    `planner.py:100`).
  - Reproduced with a manifest where `hardware_bound` is a string instead of a list:
    → `AttributeError: 'str' object has no attribute 'get'` (from
    `h.get("adapter", "?") for h in hw_bound` at `planner.py:102`).
  - Same unguarded-type pattern recurs in the HAL section: a `hardware.json` whose list
    contains a non-dict entry (e.g. `["not-a-dict"]`) → `AttributeError: 'str' object has no
    attribute 'get'` in `_hal_inventory_section`. Lower-confidence as a live scenario since
    `hardware.json` is written only by `hal_daemon.py`'s own fixed-schema
    `_write_inventory()`, unlike `manifest.json` which is a workspace file with no schema
    validation at read time — included as supporting evidence of the same gap, not a
    separate root cause.

All three crashes are 100% reproducible, one-line manifests, no exotic setup. None are covered
by the 9 new tests (which only exercise "manifest.json absent" for the missing-file case, not
"manifest.json present with a field of the wrong type").

## Verdict

**CONTRADICT.** The code works for every shape the test suite tries, and the "9 new / 23
existing" pass claim is accurate as far as it goes (I independently got 9/9 new, 600/600 on
the full non-`registry` suite). But the module's own stated contract — never raise out of
`build_system_prompt()`, because a crashing planner is worse than a status line — is false
for `manifest.json` field types that two *other* modules in this very codebase
(`workspace_report.py`, `agent_tui.py`) already treat as expected, real malformed input and
guard against for the identical fields. This diff did not reuse that existing guard, and the
gap is directly demonstrated, not speculative.

Severity is bounded by the fact that nothing currently calls `Planner.build_system_prompt()`
(confirmed above), so this cannot crash a live agent loop today — but the ticket is exactly
in the path of code that is meant to be called before every LLM call once wired in, which
elevates this from a cosmetic gap to a landmine for whoever wires it in next, and the fact
that the fix pattern already exists twice elsewhere in the same codebase removes any
justification along "the schema is trusted so this never happens" lines (`workspace_report.py`
and `agent_tui.py` evidently did not consider the schema trusted either).

Per archetype 8, this escalates to opus-5@high for judgment: whether to (a) patch
`planner.py`'s `_workspace_manifest_section`/`_hal_inventory_section` in place to adopt the
same `isinstance` guards workspace_report.py/agent_tui.py already use for `identity` and
`hardware_bound` (and the equivalent dict-entry guard in the HAL list), as a narrow
mechanical fix — the rest of the diff (HAL liveness check, descriptor masking, warm-band
windowing, non-wiring) is independently confirmed sound — or (b) treat this as a signal to
re-review wq#4509 more broadly for other unguarded-type assumptions, since the module was
built from the ticket body alone without reading the sibling modules that already solved this
exact problem.

Escalation ticket: wq#4528 ([judgment] wq#4520 verify-pass on wq#4509 CONTRADICTED -- planner.py
manifest field type-safety gap vs. workspace_report.py/agent_tui.py precedent), model
claude-opus-5, effort high, deps wq4520.
