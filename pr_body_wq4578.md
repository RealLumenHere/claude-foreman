# wq#4578 — judgment on wq#4572's contradiction of wq#4553

## What

`ops/wq4553-context-builder-live-roster/context_builder.py:88-89` — `_STATE_LINE`'s value group
goes from non-greedy `(?P<value>.*?)` to greedy, empty-safe `(?P<value>.*\S|)`. Plus two
regression tests, a pinned fixture (`fixtures/state_embedded_since_wq4578.txt`), a falsifiable
outside-population probe (`scripts/wq4578_outside_probe.py`), the ruling
(`reports/wq4578_judgment_state_line_since_collision.md`) and the bundle README's re-apply note.

## Why

wq#4572's verify-pass CONTRADICTED wq#4553/PR#2407 on one point: with `fleet_context.py`'s
broken end anchor correctly dropped, a non-greedy value group stops at the FIRST
`(since YYYY-MM-DD)`-shaped substring on a line instead of the trailing one `lumen_state.py`
appends. A role/status value whose own free text carries that idiom parsed to a truncated value
with a stale date — silently, with no error and no test covering the path.

**Ruling (a): greedy, not a new end anchor.** Greediness cannot change *which* lines match, only
where the split falls inside a line that already matches, so it fixes the binding with zero risk
of a coverage collapse. A re-added (even tag-tolerant) end anchor would buy the same binding
while taking back a dependency on the trailing-tag grammar — exactly what left
`fleet_context.py` matching 0 of 1244 live lines. `.*\S|` also avoids a plain greedy `.*`'s
trailing-whitespace capture and keeps the group a string (never `None`) for a value-less line.

**Ruling (b): the live scan ran here, clean — and the bundle was already installed.** Two
dispatch premises were stale and both were checkable from this lane:

- *"still staged, not applied"* — false. README.md:61-78 (`## Applied (wq#4570)`) records the
  install, and `lumen-run start -- grep -n "P<value>" /home/lumen/minerva/memory/context_builder.py`
  returned line 79 with the buggy pattern. The defective parser was live, so this change is
  re-applied, not merely staged.
- *"BUILD can't read live state"* — only the `--add-dir` filesystem grant is walled
  (wq#1719/wq#900). `Bash(/home/lumen/bin/lumen_state.py *)` is allowlisted:
  `lumen_state.py current` → 1244 lines, `grep -o "(since" | grep -c` → 1244,
  `grep -c "(since.*(since"` → **0**. No current state value carries an embedded since-clause,
  so the trap was latent and nothing already rendered into `claude_memory.md` was corrupted. No
  pre-install audit ticket was needed; none was filed.

wq#4572's frequency argument ("6 literal hits in kb/ISSUE-LOG.md") is corrected too: at pinned
`kb@9e3d59a3` that file has zero occurrences of the exact shape; the real count is 63 across 25
files of `claude-foreman` prose. The conclusion holds, the cited evidence did not.

## How

Pattern-local fix only — `parse_state_lines()`/`roster()`/`episodes()` logic is untouched. The
`|` empty branch keeps `groupdict()["value"]` a string so `_is_retired()`/`roster()` need no
`None` handling and no post-`strip()`.

## Testing

- `python3 -m pytest -q ops/wq4553-context-builder-live-roster/test_live_roster.py` → **9 passed**
  (7 pre-existing + `test_parse_state_lines_binds_last_since_clause`,
  `test_parse_state_lines_greedy_value_keeps_live_coverage`, which pins the same 12 facts off the
  live snapshot to prove no coverage change).
- `python3 scripts/wq4578_outside_probe.py` → exit 0. Per wq#2630 the probe draws inputs from
  OUTSIDE the fix's design population: rev-pinned fleet prose from `kb@9e3d59a3` and
  `claude-foreman@b2d4b925`, wrapped as state lines.
- **The probe can fail:** with the old `(?P<value>.*?)` restored, the probe exits 1
  (`value='Both predicates now show `'`, `since='2026-09-25'`) and pytest reports
  `1 failed, 8 passed`.
- Live: `lumen_state.py current` scan above (1244 lines, 0 embedded clauses).

## Risk / rollback

Low. One regex group in a staged-and-installed bundle; no interface, schema or SQL change. Worst
case the greedy group binds a `(since DATE)` that appears *after* the real clause inside a
trailing confidence tag — no such tag shape exists in 1244 live lines. Rollback: revert this
commit and re-run the bundle's `install.sh` via `lumen-run` (same path as wq#4570), which
restores the previous parser byte-for-byte.

`kb/ISSUE-LOG.md`: row 1543 → `fixed (wq#4578)`; row 1546 appended (don't report a capability
gap before grepping `harness/worker-settings.json` for an allowlisted wrapper).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
