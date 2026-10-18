# wq#4241 — Firmadyne/FirmAE + avatar2 for Limpet's firmware-emulation pipeline

Status: evaluation only, no install/catalog changes in this ticket. Verdict summarized in
`TOOL_REGISTRY.md`.

## 0. Grounding, and one honest gap

Both mandatory prior-art searches failed on infra, not on missing evidence:
`fleet-search web "firmadyne vs FirmAE firmware emulation 2026"` returned `ok:false` — every
general engine suspended (brave/google cse rate-limited, duckduckgo CAPTCHA, startpage
CAPTCHA) and both fallbacks dead (`BRAVE_API_KEY` unset per wq#2595, deepseek "402 — account
balance exhausted`). `fleet-search github "avatar2 QEMU GDB bridge firmware debugging"`
returned `ok:false` — `github 401, no usable token`. Both are pre-existing, already-logged
fleet conditions (`kb/ISSUE-LOG.md` row 715 / wq#2599 / wq#2601 for the search-engine outage;
no new row needed since nothing new happened here), not something this evaluation invented,
so no ISSUE-LOG append is warranted.

Given that, every claim below is sourced from one of three places, each marked inline:
1. **Live fleet evidence read this session** — the installed `firmware-emulation` skill,
   `limpet/tool-catalog.json`, and `TOOL_REGISTRY.md`'s own prior Pretender evaluation (which
   independently fetched and quoted avatar2-adjacent upstream text back on 2026-10-14, before
   today's search outage). This is the load-bearing evidence.
2. **Training knowledge** (model cutoff 2026-01), used only where (1) has no coverage,
   flagged explicitly, and listed as a re-verify item in §5 before any build ticket bundles
   anything.
3. Nothing in this doc is a guessed path/version/URL presented as verified — per instinct 8,
   anything not citable to (1) is labeled (2) and left unresolved rather than asserted.

## 1. What's already live in the fleet (verified, not guessed)

- The installed `firmware-emulation` skill already documents the automated Firmadyne/FirmAE
  path end-to-end — decision tree, prerequisites, and a direct verdict: **"FirmAE is
  recommended over Firmadyne (better success rate)."** It also already names system-mode's
  structural limit this ticket's avatar2 half maps onto: *"Require hardware supported by a
  specific QEMU board model (GPIO/MTD are not generic substitutes)"*, with an open
  Troubleshooting entry for "Wrong machine type" that has no resolution beyond "try a
  different board."
- `limpet/tool-catalog.json` already carries `qemu-user`, `qemu-system` (GPL-2.0,
  `license_risk: copyleft-source-offer`, `redistribution_posture: bundle`, `trust: core`,
  install-evidence verified live against apt on Lumen per wq#4081) and `gdb-multiarch`
  (GPL-3.0, same posture). The QEMU+GDB half of this ticket's scope is already adopted and
  shipped, independent of Firmadyne/FirmAE/avatar2.
- Grepping every entry in `limpet/tool-catalog.json` programmatically: no row exists for
  `firmadyne`, `firmae`, or `avatar2`. This is the first evaluation of all three as named
  tools in this repo — not a re-litigation of an existing verdict.
- `TOOL_REGISTRY.md:31` (Maksoor RE workstation inventory, wq#3803/wq#4012, probed live
  2026-10-14): **avatar2 1.4.7 is already physically installed** at `/opt/re-venv` on
  `maksoor`, alongside `openocd` 0.12.0 at `/usr/bin/openocd` (on the non-interactive PATH) —
  exactly the hardware-debugger bridge avatar2's "real hardware" target needs. Both rows are
  marked "not re-probed" (last confirmed 2026-10-14, not re-verified since), so "installed"
  is not the same as "confirmed working" — see §5.

## 2. Firmadyne vs FirmAE

Firmadyne (Chen et al., NDSS 2016) pioneered automated full-system firmware boot: build a
per-architecture kernel + QEMU, infer a NVRAM/network config, and boot a Linux rootfs with a
synthesized init. FirmAE (ACSAC 2020) is the actively-forked successor built specifically to
close Firmadyne's boot-success gap with better arch/kernel auto-detection and NVRAM-emulation
fixes — this is (1), the skill's own documented verdict, not a training-knowledge recall.

**Recommendation: evaluate/adopt FirmAE only.** Firmadyne is superseded by the fleet's own
already-recorded verdict; a catalog row for both would just be two entries pointing at the
same mechanism when one is already preferred. Firmadyne stays reference-only (what FirmAE
forked from), never catalogued.

## 3. FirmAE's real gate is dependency weight, not suitability

The skill's own Prerequisites section (1) requires, for the automated path specifically:
`postgresql`, `python3-psycopg2`, and `sudo` for every run (`sudo ./run.sh -r "Brand"
firmware.bin`) — a per-architecture custom kernel/QEMU build plus a live Postgres instance
tracking emulation state, run as root.

That is the same shape of blocker ISSUE-LOG #1165 (wq#3383, installing EMBA) hit: headless
build workers' `worker-settings.json` denies root-elevation and most container-engine CLIs
categorically (Tier-0, frozen for the session). FirmAE cannot be stood up unattended inside
this ticket's own sandbox for the same reason EMBA's dependency install couldn't — this is a
redispatch to a root-and-Postgres-capable host, not a suitability question, and not a
Frank-gate (instinct 3): nobody needs to touch hardware to grant a build worker root on a
disposable analysis host.

License shape (2, pending re-verify): FirmAE, as a GPLv3 fork of Firmadyne's own
GPLv2-encumbered kernel/QEMU chain, sits in the same copyleft category QEMU/gdb-multiarch
already carry in the catalog. If adopted, the posture should mirror wq#4005's EMBA verdict:
**external-invoke, never vendored** — Limpet shells out to a FirmAE install living outside
its own repo (process/CLI boundary), the same pattern that kept EMBA's GPLv3 off Limpet's own
non-GPL code.

## 4. avatar2 — a genuinely new capability, and why it doesn't repeat Pretender's failure

avatar2 appears exactly once in this repo before this ticket, as a dependency of a tool this
repo already rejected: `TOOL_REGISTRY.md:142` ("a real JTAG/SWD-debuggable MCU board,
OpenOCD, and the `avatar2` framework synchronizing the board with QEMU") in the Pretender
NO-GO writeup (wq#3877). That writeup independently fetched Pretender's own README on
2026-10-14 and quoted it directly — this is (1), not training knowledge: Pretender's setup is
*"rather fragile to set up and deploy,"* warns *"small version changes in any of these
components can cause various things not to work as expected, or behave intermittently,
particularly regarding interrupts,"* and was pinned to **avatar2 1.3.0** on Ubuntu 16.04 "as
of 9/17/2019" — frozen, EOL-era, no evidence of maintenance since. Maksoor's installed
avatar2 (§1) is 1.4.7, newer than that pin, but the fragility/version-sensitivity warning is
about avatar2's hardware-in-the-loop role generally, not specific to Pretender's old pin, and
should carry forward as a caveat rather than be dismissed as obsolete.

**Why the Qiling-adapter rejection doesn't transfer.** Both of this repo's prior
firmware-rehosting NO-GOs failed on the identical gap: `TOOL_REGISTRY.md:164` — "Pretender's
replay target is QEMU via `avatar2`, a full-system emulator with its own MMIO device-model
hooks — not Unicorn... or Qiling... already in LIMPET's stack... No adapter exists." Fuzzware
failed the same way via its angr/Unicorn binding. The surface-level pattern match ("another
tool that touches avatar2/QEMU instead of Qiling") would suggest the same NO-GO applies here.
It doesn't, because the rejected tools and this ticket's avatar2 use case aren't doing the
same job: Pretender and Fuzzware are **alternative full-system emulation backends**
competing with Qiling for the same OS/binary-emulation role, with no bridge to Qiling's
hook API. avatar2, used directly rather than as Pretender's dependency, is an
**orchestration layer over backends Limpet already adopted independently of Qiling** —
`qemu-system` and `gdb-multiarch`, both `trust: core` in the catalog (§1) for the
full-system-boot job the skill's own decision tree already separates from Qiling's
individual-binary role. There is no Qiling adapter to be missing, because avatar2 was never
competing for Qiling's job.

**The concrete gap avatar2 closes** is the one the fleet's own skill already names and leaves
unresolved: `firmware-emulation`'s system-mode section flags boards needing "hardware
supported by a specific QEMU board model (GPIO/MTD are not generic substitutes)," and its
Troubleshooting section's "Wrong machine type" entry has no fix beyond trying a different
`-M` board. avatar2's documented role (2, training knowledge — not re-verified live this
session, flagged per §0) is exactly this: forward an individual unmodeled peripheral access
from the emulated CPU to a real device over GDB/JTAG/UART and feed the result back, so a
firmware image with one or two unsupported peripherals doesn't need a full board model —
only forwarding for the gap. That is a different failure mode than Pretender's recorded
peripheral *replica* (which the field's own successor work, Conware, documents as
overfitting to the one recorded trace, per the existing Pretender writeup) — avatar2 keeps
real hardware live in the loop rather than replacing it with a static replay, so it does not
inherit that specific critique.

License (2, pending re-verify): avatar2 is Apache-2.0 upstream — permissive, the same risk
class the Pretender catalog row already carries (`license_risk: none`). If confirmed live,
avatar2 would be the first tool in this evaluation with no copyleft question at all.

## 5. Verdict

- **FirmAE**: CONDITIONAL GO as the automated boot/triage path. Suitability is settled (the
  fleet's own skill already prefers it over Firmadyne); the gate is the root+Postgres
  install wall, identical in shape to wq#3383's still-open EMBA follow-up. Needs a build
  ticket scoped to a host allowed root-elevation, not a Frank-gate.
- **Firmadyne**: no catalog row. Superseded by FirmAE per the fleet's existing verdict;
  reference-only.
- **avatar2**: GO, with two re-verify items before a build ticket bundles anything —
  (a) confirm Apache-2.0 licensing live once `fleet-search`'s engines are unsuspended (§0),
  and (b) actually probe the already-installed maksoor copy (1.4.7, `/opt/re-venv`,
  `TOOL_REGISTRY.md:31`, "not re-probed") rather than assuming install implies working,
  carrying forward Pretender's own quoted fragility/version-sensitivity warning about this
  exact framework. No root-owned service stack is required the way FirmAE needs one — it is
  a pure orchestration library driving already-core tools (`qemu-system`, `gdb-multiarch`),
  which makes it the lowest-friction adoption of the three regardless of the FirmAE gate's
  timeline.

## 6. Spec deltas for a follow-up build ticket (not applied here)

1. Add `tool-catalog.json` rows for `firmae` and `avatar2` (`category:
   emulation-rehosting`, matching the `qemu-system` row's schema at
   `limpet/tool-catalog.json`); `redistribution_posture: external-invoke` for firmae
   (process-boundary, per wq#4005's EMBA pattern), `bundle` for avatar2 pending the
   license re-verify.
2. Extend the `firmware-emulation` skill with an avatar2 orchestration path addressing the
   system-mode "wrong board model" / unsupported-peripheral gap its own Troubleshooting
   section already names without a fix.
3. Re-run `fleet-search web`/`github` once the engine suspension (wq#2595 `BRAVE_API_KEY`
   / deepseek 402 / github 401) clears, to convert every §0-flagged training-knowledge item
   into a live citation before anything ships.
4. File the FirmAE root+Postgres install as a redispatch to a root-and-container-capable
   host, mirroring wq#3383's still-open EMBA follow-up — not a block on this ticket.
