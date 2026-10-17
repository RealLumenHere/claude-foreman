# wq#4107 — verify wq#4090 (PR #2037): PCB-vision RAM budget vs the 16 GB floor

Verifier: iso-build-f85923, 2026-10-16. **Verdict: CONFIRM the PR; three findings, none in the PR's diff.**

## What PR #2037 claims
`design/wq3900_limpet_vision_component_id.md` now points its two stale 32 GB statements at `docs/pcb-vision-spec.md` §0.1/§7, which validates the YOLO + PaddleOCR + OpenVINO INT8 working set against a 16 GB floor.

## Checks and results
| Check | Result |
|---|---|
| Diff is two pointer insertions in wq3900 (commit 133f5a04, 7+/1-) | Confirmed. Wording is accurate; no new numeric claim introduced. |
| §0.3 / §7 cross-references resolve | Confirmed. wq3900 §0.3 is the "Memory budget is real" item; spec §7 is the budget; spec §7 line "not resident by default" matches the PR's "no resident VLM" statement. |
| §7 capacity budget fits 16 GB | Confirmed. Rows sum to 4.4-6.8 GB (re-added); the largest conditional (a ~5-6 GB resident 9B coder model) is handled by the §7 eviction rule. Farida idles at 753 MB used of 15,110 MB (`free -m`), so the 2.0 GB OS+services row is conservative. |
| Table arithmetic | Minor: lower bound is 4.4 not 4.5; the row labelled "Vision-stack peak" includes the 2.0 GB OS row (it is a whole-system peak); non-resident Tesseract (0.1) is counted. All conservative or cosmetic. |

## Findings (pre-existing text, not in PR #2037)
**F1 - §7.1 latency-transfer argument is unsupported, and is contradicted for the actual rig.** §7.1 says the M70q has the "same DDR generation and channel count" as the 32 GB benchmark box, so the ~0.8-2 s/board figure "transfers". Evidence:
- `ssh farida ... sudo dmidecode -t memory` (2026-10-16): 2 slots; `Controller0-ChannelA-DIMM0` = *No Module Installed*; `Controller1-ChannelA-DIMM0` = one 16 GB DDR5 SODIMM, rated 5600, configured 4800 MT/s. The 16 GB rig runs **one DIMM**.
- `research-ingestion-summary-2026-10-16.md` states only "32GB"; the benchmark's DIMM count is not recorded anywhere I could find, so "same channel count" is unevidenced.
- If the benchmark used 2x16 GB, available bandwidth is roughly double the single-DIMM rig. Since §7.1 itself argues latency is bandwidth-bound, the figure may not transfer.
The spec does require a measured run on the real box (milestone M3), so the *design* is safe; the *sentence* is overclaimed. Capacity conclusions are unaffected.
Note: filling the empty slot with a second matched 16 GB SODIMM (rather than replacing with one 32 GB stick) would give 32 GB **and** dual channel.

**F2 - cosmetic:** relabel "Vision-stack peak" to "System peak (incl. OS)" and correct 4.5 to 4.4.

**F3 - other stale "32GB M70q" statements remain** (the PR's scope was wq3900 only): `design/wq3806_limpet_architecture.md:261` (sizes a 14B model "on a 32GB / iGPU-only M70q"), `design/wq3929_competitive_analysis.md:141,154` ("32GB/iGPU ceiling"), `docs/limpet_vision.md:27` ("M70q Gen 5, i5-14500, 32GB DDR5"). `docs/settings-spec.md:63` already notes the 32 GB figure is a target, not a measured value.

## Not done / limits
No PaddleOCR/YOLO/OpenVINO working set was measured; those rows are the spec's estimates. No latency measurement was attempted (needs the M3 run on Farida).
