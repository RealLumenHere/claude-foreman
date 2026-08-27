# wq#1006 — research phase gate for hardware-RE projects

Wires the previously declarative `phase_gates: [research, ...]` in
`.fworker-rules.yaml`: a project with `hardware_class` set now REQUIRES a
non-empty `projects/<name>/research/PRIOR_ART.md` before any device-interaction
action (adb, fastboot, heimdall, edl, flashrom, mtkclient, esptool, openocd,
dfu-util, stm32flash, avrdude, serial consoles, dd raw-device reads). RED,
same class as red_patterns. Enforced in harness/action_gate.py (Layer 1 hook),
never in the agent's hands.

Mechanics:
- compile_rules() now emits `phase_gates` (accumulated, top-level form also
  honored) and `prior_art_paths` (canonical projects/<name>/research/ first,
  then research/ beside each rules file). Hook checks paths LIVE, so a
  fleet-search run mid-session opens the gate without recompile.
- Gate condition: hardware_class set AND "research" in phase_gates.
- Ordered BEFORE the flash/local-backup path: a verified backup does not
  excuse skipping research.
- Satisfaction path: `fleet-search --project <name> ...` (wq#999), which
  writes PRIOR_ART.md. A single standalone fleet-search command is exempt
  from the device-token match (its query may say "adb"); any ;|&& chaining
  voids the exemption.
- Tamper: hand rm/cp/mv/tee/touch/redirect/sed-i onto PRIOR_ART.md, or
  Write/Edit tool on it, is RED (same class as .fworker-rules tamper).
- Token-presence matching, same posture as guard.sh: `grep adb notes.md`
  blocks too while unsatisfied. Accepted: one fleet-search opens the gate.
- guard.sh untouched by design: it is fixed house denials; per-project rule
  logic lives in action_gate.

Test to failure (real miko3 rules, no research/ dir exists):
- adb shell getprop -> exit 2, reason names the missing PRIOR_ART path and
  the fleet-search command.
- fastboot flash -> exit 2 (research gate, before the backup check).
- standalone fleet-search with "adb" in query -> exit 0.
- identical rules + non-empty PRIOR_ART.md (scratch copy; real project dir
  untouched) -> adb exit 0.
pytest: tests/test_action_gate.py 55/55 (11 new wq#1006 cases incl.
empty-file blocks, chain-not-exempt, tamper, no-hardware_class passthrough).
Pre-existing test_local_rung/test_fworker_integration failures reproduce
byte-identically on the pre-change backup — unrelated.
