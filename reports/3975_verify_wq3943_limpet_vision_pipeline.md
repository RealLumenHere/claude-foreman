# wq#3975 — verify-pass on wq#3943 (LIMPET BUILD: vision pipeline plugin — webcam/HDMI capture to component identification)

PR: `claude-foreman#1920` (http://100.121.17.5:3000/lumen/claude-foreman/pulls/1920),
commit `a12b8a9e` on branch
`feat/wq3943-limpet-build-vision-pipeline-plugin-webc-a1`, diffed against
merge-base `a01a1695` (15 files, 1483 insertions / 9 deletions). Author:
claude-sonnet-5 · Verifier: claude-sonnet-5@high (fresh instance, wq#3975).

## Claim under test

wq#3943 claims to have built the LIMPET vision pipeline plugin per its 7
numbered components: (1) v4l2 frame capture (`capture_ops.py`), (2) Ollama
VLM integration (`vision_ops.py`), (3) component lookup via FlashDetector/
Part-DB (`component_lookup_ops.py`), (4) a structured `components.json`
manifest (`manifest_ops.py`), (5) 4 MCP tools (`server.py`), (6) HAL
webcam/HDMI auto-detect (`hal.detect_video_devices()` in
`ops/wq3809-limpet-hal/hal.py`), (7) a `limpet-plugin.toml` manifest update
(`limpet/examples/component-id.toml`) — plus a wizard-contract adapter and a
genuine (not bare-exists) health probe. Done-notes claim 74 tests pass.

## What I checked

1. **Read the diff cold** against the true merge-base (`a01a1695`, not a
   stale `main` two-dot diff, which drags in ~150 unrelated files from branch
   divergence and is not the PR's actual change). Confirmed the 15-file/
   1483-insertion shape matches the done-notes exactly: four new ops modules,
   `server.py`, `selftest.py`, `README.md`, `requirements.txt`, 4 test files,
   plus the pre-existing `hal.py`/`hal_test.py`/`component-id.toml` edits.

2. **Ran the probe and full test suite** (detached to the PR tip in the
   verify worktree, no real hardware/Ollama/FlashDetector/Part-DB anywhere on
   this box):
   - `python3 -m pytest ops/wq3943-limpet-vision-pipeline-a1/tests -q` → 43
     passed.
   - `python3 -m pytest ops/wq3809-limpet-hal/tests -q` → 31 passed.
   - 43 + 31 = **74**, matching the done-notes' "74 tests pass" exactly.
   - `python3 ops/wq3943-limpet-vision-pipeline-a1/selftest.py` → exit 0,
     `OK: component-id pipeline wiring self-test passed` — this is
     `limpet/examples/component-id.toml`'s `[health].command` verbatim, so
     the actual health probe (not a stand-in) was exercised and it can
     genuinely fail (confirmed: temporarily breaking `flash_marking_decode`'s
     Winbond prefix regex made it fail with the expected `FAIL:` message and
     exit 1, then reverted — this is not a bare "import succeeded" probe).
   - `python3 -m py_compile` on all 6 new/changed `.py` files: clean.

3. **Cross-checked every external citation against the committed sources it
   claims to follow** (the fabrication check, instinct 8):
   - `research/WQ3922_VISION_HDMI_CAPTURE.md` §G-b/§G-c/§2b/OUTPUT sections
     all exist at the cited line numbers and say what the PR says they say.
     `capture_ops.capture_frame`'s two ffmpeg invocations
     (`-f v4l2 -video_size ... -i ... -frames:v 1 -update 1` and the
     `-input_format mjpeg ... -frames:v 2` MJPEG-warmup variant) are verbatim
     matches of that doc's §G-c example commands, not paraphrased/invented.
   - `limpet/hardware.d/06-hdmi-capture.toml` and `07-usb-webcams.toml`:
     the new MS2109 (`534d:2109`) / MS2130 (`345f:2130`) VID:PID pairs added
     to `hal.py`'s `REGISTRY`, and the "generic UVC webcams have no stable
     VID:PID by design" claim motivating `detect_video_devices()`, both match
     the catalog files exactly (confidence `low`/`high` as stated).
   - `design/wq3900_limpet_vision_component_id.md` §6: the `components.json`
     schema (`manifest_ops.py`'s `run_pipeline`) matches the design doc's
     example structure field-for-field, including the deliberately minimal
     `unresolved: [{"id", "markings"}]` shape (§6's own example, not a
     shortcut taken by the implementation).
   - `qwen2.5-vl:7b` default model: matches the research doc's OUTPUT
     recommendation exactly, and the PR's claim that it is "not pulled on
     this box" is independently plausible — `ollama` isn't even reachable
     from this sandbox, so `vision_ops.identify_components` would legitimately
     degrade, and the code path for that (`HTTPError`/`URLError` → `{"ok":
     False, "error": ...}`, never a raise) is directly exercised by
     `test_identify_components_handles_model_not_pulled` /
     `_handles_unreachable_ollama`.
   - `mcp` package: confirmed genuinely absent (`import mcp` →
     `ModuleNotFoundError`) on this box, consistent with the README's claim
     that the ops modules are split out specifically to stay testable without
     it.

4. **Tried to break the hallucination-control claims** (§4/§6's "never
   upgrade a miss to a guessed identity" is the one property this pipeline
   must get right, per the design doc's own evaluation plan). Drove
   `component_lookup_ops.lookup_part` and `manifest_ops.run_pipeline`
   directly with adversarial inputs beyond the test suite's own cases:
   unresolvable markings, empty `markings` lists, and multiple-Part-DB-hit
   ambiguity — all correctly land in `unresolved`/`needs_review: true` with
   `resolved: None`, never a fabricated part. No injection risk in any
   `subprocess.run` (`shell=False`, marking passed as a single argv element)
   or in `partdb_search` (`urllib.parse.quote`-escaped query).

5. **Found one real, demonstrable gap** (see Non-blocking observations):
   wrote a one-off script round-tripping a component with `pin_count: 8`
   through `vision_ops.identify_components` (monkeypatched) →
   `manifest_ops.run_pipeline` and inspected the written manifest entry —
   `pin_count` is silently absent from the output. Confirmed this is not
   test-order-dependent or a transcription slip on my part by printing the
   full entry dict.

## Verdict

**CONFIRM.** Every file in the diff is accounted for, the probe
(`selftest.py`, the actual `[health].command`) passes and is demonstrably
able to fail, the full test suite reproduces the claimed 74/74, and every
external citation (research doc ffmpeg commands, hardware-catalog VID:PID
pairs, design-doc schema, model choice) checked out against the real
committed source rather than being a plausible-sounding invention. The
hallucination-control property the design doc treats as the one that must be
strict held under adversarial inputs I constructed myself, beyond what the
shipped tests already cover. Nothing here rises to a contradiction under the
ticket's judgment-trigger bar — no escalation to opus-5@high.

## Non-blocking observations (not part of the verdict)

- **`pin_count` is captured by the VLM but never surfaces in the manifest**
  (`ops/wq3943-limpet-vision-pipeline-a1/manifest_ops.py` — the `entry` dict
  built in `run_pipeline` reads `bbox`, `package`, `markings`, `type_guess`,
  `confidence`, `probable_function` off each VLM-returned component but never
  `comp.get("pin_count")`, even though `vision_ops.RESPONSE_SCHEMA` prompts
  the model to report it and the field is listed in the schema's
  `properties`). wq#3943's own ticket text explicitly lists "pin count" as a
  required manifest field ("Output: structured component manifest (JSON)
  with component name, function, datasheet URL, pin count, package type,
  confidence score"). The implementation instead follows
  `design/wq3900_limpet_vision_component_id.md` §6's schema verbatim, and
  that design doc's own example manifest entry *also* omits `pin_count` — so
  this is a design-doc/ticket-text mismatch that predates this PR, not an
  implementation slip against its stated design target. Still, the net
  effect is real: data the VLM is explicitly prompted to produce is computed
  and then silently dropped, with no `[future work]`/`[verify-at-adoption]`
  flag calling this out the way the PR flags every other gap (VLM not
  pulled, FlashDetector not adopted, Part-DB not adopted, golden-board
  fixture pending). Low severity — no test depends on `pin_count` surviving,
  and nothing downstream currently reads it — but worth a follow-up ticket or
  a one-line fix (`"pin_count": comp.get("pin_count")` in the `entry` dict)
  before the Target Device Wizard (wq#3901) starts consuming these manifests
  and expects it.
- **Health-probe honesty claim, verified rather than taken on faith**: the
  README asserts `selftest.py` "can genuinely fail, unlike a bare
  binary-exists probe." I didn't just read that claim — I broke the
  underlying resolver and watched the probe fail with the expected message,
  then reverted. The claim holds.
