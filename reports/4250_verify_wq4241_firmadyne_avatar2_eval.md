# wq#4250 — Adversarial verify-pass of wq#4241 (PR#2164: EVALUATE Firmadyne+QEMU/avatar2 for Limpet)

**Verdict: CONFIRM** — the PR is a docs-only evaluation (no install/catalog/skill changes),
its load-bearing citations check out exactly against live fleet files, and its two
training-knowledge items are explicitly hedged rather than asserted as verified. No
fabricated evidence (instinct 8) found.

## What was shipped (PR#2164, squash-merged to main as `67e2bb13`, originally `0c0465436f`
on the PR branch — `git diff 0c0465436f 67e2bb13 -- TOOL_REGISTRY.md research/WQ4241_*.md`
is empty, confirming the squash preserved content byte-for-byte)

Two new files, no code: `research/WQ4241_FIRMADYNE_AVATAR2_EMULATION_EVAL.md` (169 lines) and
a ~27-line section appended to `TOOL_REGISTRY.md`. Verdict: FirmAE conditional-GO (suitability
settled, gated on a root+Postgres install wall), Firmadyne no catalog row (superseded by
FirmAE per the fleet's own skill), avatar2 GO pending a license re-verify and a live re-probe
of the already-installed Maksoor copy. `limpet/tool-catalog.json` has zero rows for any of
the three (`grep -in "firmadyne|firmae|avatar2" limpet/tool-catalog.json` — empty), matching
the ticket's own "evaluation only, no install/catalog changes" framing. The ticket's title
says EVALUATE (detail field's "DEPLOY ... pipeline ... Full config in playbook" text is
dispatcher boilerplate, not a Frank-issued override — matches the sibling convention: wq#4238
(Loki+Promtail), wq#4240 (promptfoo) both landed as `docs(research)` writeups, not
deployments).

## Probe

No code/tests ship with a docs-only PR, so the probe is citation verification against the
live files each claim depends on — the one thing that *can* fail here is a quote or line
number that doesn't actually exist:

- **`firmware-emulation` skill** (load-bearing per the writeup's own §0 evidence hierarchy):
  invoked the skill directly and diffed its text against every quote. `"FirmAE is
  recommended over Firmadyne (better success rate)."` — exact match. `"Require hardware
  supported by a specific QEMU board model (GPIO/MTD are not generic substitutes)"` — exact
  match. Prerequisites' `sudo apt-get install postgresql python3-psycopg2` and
  `sudo ./run.sh -r "Brand" firmware.bin` — exact match. Troubleshooting's "Wrong machine
  type" entry resolves only to `-M versatilepb`/`-M virt` (generic boards) — confirms the
  writeup's claim that it has "no fix beyond trying a different `-M` board." avatar2 is
  **not** mentioned anywhere in the skill; the writeup correctly labels avatar2's gap-filling
  role as training-knowledge (2), not a skill citation.
- **`TOOL_REGISTRY.md:31`** (Maksoor inventory): live grep confirms
  `oscura / wireskein / sootty / avatar2 | 0.0.1 / 0.0.16 / 1.0 / 1.4.7 | /opt/re-venv | not
  re-probed`. Matches the writeup's "avatar2 1.4.7... not re-probed" exactly.
- **`TOOL_REGISTRY.md:142`/`:164`** (Pretender NO-GO): live grep confirms the JTAG/SWD
  hardware-in-the-loop quote, the "rather fragile to set up," "avatar2 1.3.0... Ubuntu 16.04...
  as of 9/17/2019," and "No adapter exists from Pretender's inferred register models to
  Qiling's hook-based peripheral API" lines verbatim. Line numbers are off by ~1 from the
  cited values (current file has drifted slightly since 2026-10-14), not a fabrication.
- **ISSUE-LOG row 715 / wq#2595/2599/2601** (search-outage grounding): confirmed in
  `kb/ISSUE-LOG.md:823`. Independently re-ran both searches myself this session:
  `fleet-search web "test query verify wq4250"` → `ok:false`, same four suspended engines
  (brave/duckduckgo/google cse/startpage) plus the same two dead fallbacks (`BRAVE_API_KEY`
  unset, deepseek 402); `fleet-search github "avatar2 test"` → `ok:false, github 401, no
  usable token`. The outage the writeup used to justify its §0 training-knowledge fallback is
  real and still live, not an invented excuse to skip prior art.
- **wq#4005 EMBA precedent** (license-posture analogy): `design/wq4005_emba_dissection.md`
  confirms GPLv3 + process-boundary/never-vendor, matching the writeup's "mirror wq#4005's
  EMBA verdict: external-invoke, never vendored."
- **ISSUE-LOG #1165 / wq#3383** (root+Postgres analogy): confirmed at
  `kb/ISSUE-LOG.md:1314` — `installer.sh` requires `EUID==0`, worker-settings denies
  root-elevation categorically, filed as a redispatch not a block. Same shape FirmAE's
  Postgres+sudo requirement would hit.

## Adversarial checks that did not break it

- **Instinct 8 fabrication check**: every citation above resolves to real, currently-grepped
  text at (approximately) the cited location — none of the quoted strings are inventions.
  The two items honestly marked training-knowledge (FirmAE/avatar2 license classes) are
  *not* asserted as verified and are explicitly queued as re-verify items in §5/§6 rather
  than smuggled in as fact — correct handling of the instinct, not a violation.
- **Scope-dodge check**: could the author have used "EVALUATE" framing to avoid the harder
  "DEPLOY ... Docker container" work implied by the ticket `detail` field? No — every other
  EVALUATE-titled ticket in recent history (wq#4238, wq#4240) landed the same way
  (docs-only), so this is the established convention for this ticket shape, not a dodge
  unique to this author.
- **Logic check on the avatar2 NO-GO-transfer argument**: Pretender/Fuzzware were rejected
  for lacking a Qiling/Unicorn adapter because they *compete with* Qiling for the
  binary-emulation role. avatar2 used directly instead orchestrates `qemu-system` +
  `gdb-multiarch`, both already `trust: core` in `limpet/tool-catalog.json` independent of
  Qiling — confirmed via grep, these two rows exist and carry that trust tier. The
  distinction (orchestration layer vs. competing backend) holds up; it isn't a rebranding of
  the same rejected mechanism.
- **"Done" notes vs. board**: `wq show 4241` notes cite commit `0c0465436f`; the actual main
  commit is `67e2bb13` (squash-merge renamed the SHA). Content-identical per the diff above —
  cosmetic only, not a correctness issue.

## Verdict

**CONFIRM.** No contradiction found; escalation to opus-5@high not triggered.
