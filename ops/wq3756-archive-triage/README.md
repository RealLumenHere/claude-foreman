# wq#3756 — archive triage

One-time mining pass over `/home/lumen/minerva/minerva.db`'s `proposals` table
(1881 rows) for buried hardware-relevant value.

Run: `python3 ops/wq3756-archive-triage/triage.py` — read-only against minerva.db,
writes `REPORT.md` next to this file.

Pipeline (`triage.py`):
1. drop proposals older than 6 months (`step1_recent`)
2. drop spam-keyword matches: credential, restore, LiteLLM config, SaaS, cloud (`step2_despam`)
3. cluster survivors by keyword into the five watch categories (`step3_cluster`)
4/5. score survivors against the named hardware (RK3399, Mac Pro 6,1, BC-250, MT8167,
   Ingenic T40) plus category hits (`step4_5_score`)
6. drop anything already locally tested (deployed/shipped/piloting/applied_unverified),
   rank by score with a per-signature diversity cap so one repetitive cluster can't
   crowd out the rest, emit the top 30 (`step6_top`)

Verified finding: a direct substring search over all 1881 rows (not just the
filtered set) turns up zero mentions of RK3399, MT8167, Ingenic T40, PiKVM,
MaskROM, or Rockchip — the proposal archive has never produced an
Ingenic/MediaTek-firmware, PiKVM-alternative, or Rockchip-MaskROM proposal.
The real hits are BC-250 (hapi) and Mac Pro 6,1 (nephthys) local-LLM-optimization
and never-benchmarked-eval proposals — see `REPORT.md`.
