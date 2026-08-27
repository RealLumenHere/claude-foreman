# RESEARCH-FIRST PHASE GATE — design note (wq#991)
_2026-08-27. Extends TIERED_INTELLIGENCE_DESIGN.md Layer 3 (wq#985). Reuses
fleet-search (wq#999) as the search surface — no new crawl tooling._

## ROOT CAUSE (from the ticket, 4 documented incidents)
Agents interact with irreplaceable hardware before doing the 2-3 web searches
that would have shown the community already solved it (Miko: r/hacking + OpenMiko;
RK3568: board-state + failed-image threads; Nest: unlock writeups; Meta: same).
The design doc already declared `phase_gates: [research, planning, implementation]`
in .fworker-rules.yaml, and compile_rules() already PARSES it — but nothing
ENFORCES it. `phase_gates` was a dead key. This is the missing enforcement.

## CLASS OF DEFECT
"First device contact before prior-art research" on a hardware_class project.
Same adversary model as the whole tiered design: the agent is confident and will
skip an optional checkpoint. So the gate must be MECHANICAL (in the harness,
outside the agent) and must gate READ-only device contact too, not just
destructive contact — the value of research is BEFORE any poking, and the Miko
wipe was preceded by ordinary-looking adb contact.

## WHERE THE CLASS ALSO LIVES
Every hardware-RE project: miko3 (live), plus Screenpad/RTD, BC-250/hapi, Quest,
Nest, RK3568, Meta. All get the gate for free the moment their rules file sets
`hardware_class` or lists `research` in `phase_gates`. miko3 already does both.

## THE CHANGE THAT MAKES THE CLASS IMPOSSIBLE (not this instance)
A new gate stage in action_gate.py hook_main, checked AFTER RED (RED still wins)
and BEFORE any AMBER/GREEN device command executes:

  research-gated project?  ->  hardware_class set OR "research" in phase_gates
  research complete?       ->  valid marker at projects/<p>/research/.research-complete.json
  is this a DEVICE command? ->  builtin DEVICE list + per-project device_patterns
  if gated AND not complete AND device command  ->  BLOCK (exit 2), self-service msg

Non-device commands (read files, inspect dumps, git, write a plan, run the
research tool itself) are NOT blocked during the research phase. Only commands
that drive the physical device are.

### Device command = drives the device (broad, read included)
adb, fastboot, heimdall, edl/qdl/firehose, flashrom, rkdeveloptool/upgrade_tool,
mtk/mtkclient/sp_flash, uuu, esptool, openocd, avrdude, and serial consoles
opening /dev/tty* (screen/minicom/picocom/cu/tio, cat/redirect on a tty).
Host-observation (lsusb, dmesg, ls /dev) is NOT gated — that is how you identify
the device, and blocking it is pointless friction. Per-project `device_patterns`
extends the list additively (same posture as red_patterns/amber_patterns).

## SELF-SERVICE, NOT ESCALATION
Unlike RED (which escalates to a reviewing agent and cannot be retried), the
research block is a prerequisite the agent clears itself:
  python3 harness/research_phase.py --project <name>
runs 3-5 targeted queries through fleet-search (web + reddit + github), reads the
top hits, writes projects/<name>/research/PRIOR_ART.md, and — only if >=1 source
returned results — writes the .research-complete.json marker. Then the device
command passes. Fail-closed: total search outage writes NO marker and exits
non-zero, so "no research" can never read as "research done."

## ANTI-FORGERY (the agent must not fake the marker)
1. Direct mutation of .research-complete.json via Bash (>, tee, cp, mv, touch,
   echo/printf, python -c open(...,'w')) or via Write/Edit is RED (gate-tamper),
   exactly like .fworker-rules.yaml. The agent cannot create the marker by hand.
2. The only writer is research_phase.py, which writes via open() inside a
   subprocess — invisible to the PreToolUse hook by design — AFTER real results.
3. The validator requires: marker parses, schema == research-complete/v1, project
   matches, and the referenced findings file exists and is >= 200 bytes. A bare
   touch (even if it slipped past #1) fails validation.
Marker sha is NOT pinned to the live findings file: fleet-search legitimately
appends to PRIOR_ART.md later, which would otherwise invalidate a real marker.

## MARKER HOME = canonical project dir (not the worktree)
projects/<name>/research/ — the same dir fleet-search hardcodes. So research
done once persists across sessions/worktrees (ticket #5: future agents don't
re-search). compile_rules records project + project_root so the gate finds it.

## TEST TO FAILURE
Unit (tests/test_action_gate.py, no network): fresh miko3 rules + no marker ->
`adb shell getprop` BLOCKED with "research" in msg; valid marker -> same cmd
passes; `touch .../.research-complete.json` -> RED; a still-destructive device
cmd (`fastboot flash`) stays RED with/without research; a project with no
hardware_class + no research phase_gate -> device cmd NOT blocked (no regression
on non-hardware tickets); non-device cmd (`cat dump.bin`) not blocked pre-research.
Template (tests/test_research_phase.py): injected fake search -> marker written +
PRIOR_ART.md populated; all-sources-fail -> NO marker, non-zero exit.
Live: one real `research_phase.py --project miko3` run, then confirm the gate
flips a simulated fresh session from BLOCKED to allowed.

## ROLLBACK
Pure additive code in action_gate.py + one new file research_phase.py. Revert =
drop research_gate_reason() call in hook_main (one line) or revert the PR. No
data migration, no service restart (hook is read fresh per tool call).
