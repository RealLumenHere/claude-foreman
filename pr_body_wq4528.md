# wq#4528 — planner.py adopts the sibling isinstance guards it was built without

## What

`limpet/planner.py` now type-checks every field it reads out of `manifest.json` and
`hardware.json` at its point of use, so `Planner.build_system_prompt()` honours the
never-raise contract its own module docstring states. 16 new tests in
`limpet/test_planner.py` pin each shape.

## Why

wq#4520 verified wq#4509 (Limpet dynamic planner, PR claude-foreman#2374) and returned
CONTRADICT. The module docstring promises every section degrades to a plain status line
rather than raising — "a planner that crashes because the box has no USB devices
attached is worse than one that says so in the prompt" — but
`_workspace_manifest_section` read `identity` and `hardware_bound` without the
`isinstance` guard that `workspace_report.py:313,315` and `agent_tui.py:114` already
apply to those exact two fields.

The ticket offered two paths: (a) patch the two sections mechanically, or (b) treat the
miss as a signal to re-review the module more broadly, since it was written from the
ticket body alone — ISSUE-LOG #1518 records that the spec and any existing planner were
unreachable from that worker's sandbox, so the sibling modules that had already solved
this problem went unread.

**Taken: (a) as the deliverable, with (b)'s audit bounded to planner.py's 116 lines.**
That bound is the point. The rest of wq#4509's diff is independently confirmed sound
(HAL liveness check, descriptor masking, 9 passing tests, 600/600 on the suite at the
time), so re-reviewing it wholesale is the re-implementation the ticket forbids. But the
*cause* of the miss was not reading siblings, and that cause is module-wide rather than
confined to two lines. Auditing the module cost one script and found more than twice the
reported defects.

### What the audit found

Baseline, pre-fix, reproduced with a `python3 -I` script over 8 shapes:

| shape | result before |
|---|---|
| `identity` as JSON list | `AttributeError: 'list' object has no attribute 'items'` |
| `identity` as JSON string | `AttributeError: 'str' object has no attribute 'items'` |
| `identity` as number | `AttributeError: 'int' object has no attribute 'items'` |
| `hardware_bound` as JSON string | `AttributeError: 'str' object has no attribute 'get'` |
| `hardware_bound` as JSON object | `AttributeError` |
| `hardware_bound` with non-dict entries | `AttributeError` |
| `hardware.json` with non-dict entries | `AttributeError: 'str' object has no attribute 'get'` |
| device `descriptor` non-string | `TypeError: expected string or bytes-like object, got 'int'` (inside `wr.mask_text`'s `re.sub`) |
| device `capabilities` with non-string entries | `TypeError: sequence item 0: expected str instance, int found` |

wq#4520 cited three of these; the other four fall out of the same unread precedent.
None are reachable only via a code bug: `manifest.json` is operator-editable (that is
why two sibling modules guard it), and `hardware.json` is a file on disk that can be
truncated or hand-edited whatever `hal_daemon.py`'s writer intends.

`capabilities` as a bare string was the one shape that did not raise — it rendered
`capabilities=[s, w, d]`, joining the string's characters. Guarded too, since it is the
same field.

## How

In `limpet/planner.py`, at each point of use, in the sibling idiom:

- `identity` / `hardware_bound` — `isinstance` checked exactly as
  `workspace_report.py:313,315` does.
- `hardware_bound` entries — a non-dict entry renders `?`, so a malformed entry still
  tells the model something is bound but unreadable rather than vanishing.
- `_read_hal_inventory` — filters to dicts, which makes its existing `list[dict]`
  return annotation true and keeps `_hal_inventory_section` free of per-field checks.
- `descriptor` — coerced with `str()` before `mask_text`, so masking still runs.
- `capabilities` — `isinstance` list check, entries coerced with `str()`.
- `_warm_band_section` — `read_blocks` wrapped in `except OSError`. This is the one
  change beyond type guards: `read_blocks` tolerates malformed *lines* but still
  `open()`s the path, so an unreadable evidence log raised through the same contract the
  other two sections already protect with `except OSError`. Two lines, same defect
  class, so it is in rather than left as a follow-up.

## Testing

- `limpet/test_planner.py`: 25 pass (9 from wq#4509, 16 new).
- `limpet/`: 751 pass (the suite has grown past wq#4509's 600 since wq#4511 landed).
- 14 of the 16 new tests fail against pre-fix `planner.py`, checked by restoring
  `HEAD:limpet/planner.py` under the new test file — the probes can fail, which was
  wq#4509's problem: its 9 tests only exercised each source's *absent* extreme, never a
  source present with the wrong type, so they could not have caught this. The 2 that
  pass both ways (`descriptor-null`, descriptor masking) are deliberate regression
  guards on behaviour the `str()` coercion could have broken, not defect probes.
- Probe inputs sit outside the population the fix was scoped to (wq#2630): wq#4520
  scoped the gap to `manifest.json`, so the `hardware.json` device-entry, `descriptor`
  and `capabilities` probes come from the other source entirely — and those are where
  the four unreported defects were.

## Risk / rollback

Low. The change is confined to `limpet/planner.py`'s three section builders and only
adds guards; no call signature, output format, or happy-path render changes — the 9
wq#4509 tests that pin the populated and absent renders pass unmodified. Nothing calls
`Planner.build_system_prompt()` yet (zero hits outside `planner.py`/`test_planner.py`),
so no live agent loop could have crashed on this today; that is also why it was worth
fixing before it sits in front of every LLM call. Rollback is a revert of this commit,
which restores the pre-wq#4528 behaviour exactly.

Full prior evidence: `reports/wq4520_verify_wq4509_dynamic_planner_hal_workspace.md`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
