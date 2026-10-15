# wq#4051 — verify wq#4012 (TOOL_REGISTRY.md drift vs lumen_state maksoor)

Archetype 8 adversarial verify. PR: http://100.121.17.5:3000/lumen/claude-foreman/pulls/1994
(already merged to main as 00bbc369, merge of bfe5e86e). Author: claude-sonnet-5. Verifier:
claude-sonnet-5@high, fresh instance.

## Verdict: CONFIRM

No contradiction found. No escalation to opus-5@high.

## What the diff does

- `TOOL_REGISTRY.md`: adds 8 rows (capa, UEFITool, Renode, angr, oscura/wireskein/sootty/avatar2,
  Gepetto/Diaphora, EMBA, Docker CE) to the Maksoor RE workstation table, sourced from
  `lumen_state`'s `maksoor.re_toolset` fact (set 2026-10-13), which the table had drifted behind
  by two days. Also annotates the existing REA row with the `maksoor.rea_mcp` cross-check and a
  pointer to the REA section's separate 6.4.0→6.5.0 Lumen-side drift note.
- `scripts/check_maksoor_tool_registry_drift.py`: new probe — reads `maksoor.re_toolset` via
  `lumen_state.py current`, extracts Phase 1-3 tool names, and fails if any name isn't mentioned
  (case-insensitive substring) in the Maksoor section of `TOOL_REGISTRY.md`.

## Checks performed

1. **Ran the probe as shipped**: `python3 scripts/check_maksoor_tool_registry_drift.py` →
   `OK: all 12 distinct maksoor.re_toolset Phase 1-3 tool names are present` (exit 0).
2. **Cross-checked every added row against live `lumen_state.py current maksoor re_toolset`**
   output — names, versions, and locations in the diff match the fact verbatim (capa 9.4.0
   /usr/local/bin, UEFITool A72 suite /opt/UEFITool, Renode 1.17.0 /opt/renode, angr 10.0.3
   /opt/angr-venv, oscura 0.0.1/wireskein 0.0.16/sootty 1.0/avatar2 1.4.7 in /opt/re-venv,
   Gepetto+Diaphora as Ghidra extensions, EMBA repo+Docker image, Docker CE 29.9.0). No
   fabricated values (instinct 8 check: every value traces to the live fact).
3. **Verified the REA cross-reference claim** — `lumen_state.py current maksoor rea_mcp` does
   say 6.4.0 (matches), and `TOOL_REGISTRY.md`'s REA section (line 48/50) does document the
   separate Lumen-side 6.4.0→6.5.0 drift (wq#4006) that the new annotation points to. Not a
   fabricated pointer.
4. **Falsifiability probe (wq#2630)**: stripped the `capa` row from a scratch copy of
   `TOOL_REGISTRY.md` (outside the repo, `/tmp/fworker-wq4051/drift_probe_scratch`) and reran the
   probe's `main()` against it → correctly reported
   `DRIFT: ... missing tools ... : capa` and exited 1. The probe can fail, and fails on the
   right input — not vacuous.
5. **Convention check**: sibling `scripts/ollama_toolprobe.py` also ships with no unit test and
   is not wired into CI — the new script isn't a convention regression. It's documented as a
   manual-rerun step in `TOOL_REGISTRY.md` ("Run ... after any future `maksoor.re_toolset`
   change"), not claimed to be automated, so no gap between claim and reality.
6. **Markdown table integrity**: new rows render cleanly, no stray `|` characters breaking the
   table.

## Minor non-blocking observations (not contradictions)

- `phase13_tool_names`'s per-token regex (`^\s*([A-Za-z]+)`) truncates trailing digits, so
  `avatar2` is extracted as `avatar`, and the missing-tool check is substring-based
  (`n.lower() not in section`) rather than exact-token. Neither caused a wrong result against
  current content (checked for incidental substring collisions in the Maksoor section — none
  found), but a future tool name that's a bare substring of unrelated text could in theory
  produce a false "present". Not worth blocking on; flagging for whoever next touches the parser.
- The script is scoped tightly to the current `maksoor.re_toolset` fact-text format (splits on
  `". DEFERRED:"`, strips a fixed prefix). A future reformat of that fact would need the parser
  updated too — expected maintenance, not a defect in this change.

## Probe commands (for re-run)

```
python3 /home/lumen/worktrees/wq4051-a2/scripts/check_maksoor_tool_registry_drift.py
/home/lumen/bin/lumen_state.py current maksoor re_toolset
/home/lumen/bin/lumen_state.py current maksoor rea_mcp
```
