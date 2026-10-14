# wq#3886 — adversarial verify + judgment on wq#3877 (Pretender peripheral-modeling eval)

- **Ticket verified:** wq#3877, merged `4726e0f3` (rebase of `b44c3c92ce`) on main,
  `docs(tools): wq3877 evaluate Pretender for automated peripheral modeling`
- **Verifier:** claude-sonnet-5@high (fresh instance, worktree `wq3886-a1`, 2026-10-14)
- **Escalation:** opus-5@high (contradiction found → judgment trigger)
- **Verdict: CONTRADICTION CONFIRMED (narrow, documentation-only — the headline NO-GO
  recommendation itself holds up).** Follow-up fix ticket filed: wq#3889.

## Scope

wq#3877 is a docs-only change: 52 lines added to `TOOL_REGISTRY.md` (lines 92-143),
recording a NO-GO verdict on adopting Pretender (UCSB seclab, RAID 2019) for LIMPET's
automated peripheral modeling. No code, no tests — "running its probe" here means
independently reproducing every externally-checkable claim and checking every
repo-internal claim against the files it cites.

## What's right (independently re-verified, not trusted from the doc)

- **Pretender GitHub README quotes** (`fleet-search read
  https://github.com/ucsb-seclab/pretender`): "rather fragile to set up and deploy,"
  the interrupt/version-drift warning, and the USB-bus-controller nondeterminism line
  all appear verbatim in the live-fetched README.
- **avatar2 1.3.0 / Ubuntu 16.04 / "as of 9/17/2019" pin**: confirmed verbatim in the
  same README ("We tested with version 1.3.0 ... We performed all experiments on a
  system running Ubuntu 16.04").
- **Conware quote**: `fleet-search web "Conware Pretender accuracy limitations stateful
  peripheral model comparison"` returns the PurS3 Lab Conware PDF with the snippet
  "Pretender is incapable of replaying peripherals that were not ..." — matches the
  doc's quote and attribution exactly.
- **QEMU/full-system-emulator framing**: `fleet-search web` on the original Pretender
  paper confirms its abstract states inferred models are "leveraged by popular
  full-system emulators (e.g., QEMU)" — supports the doc's claim that Pretender targets
  a different emulation layer than Qiling/Unicorn, with no existing adapter.
- All four bullets under Recording/Accuracy/Integration are independently reproducible
  from live sources, not paraphrased-and-trusted.

## Contradiction found

`TOOL_REGISTRY.md:94-96` and `:139` assert, as present fact:

> Stays at the "community" catalog tier (`limpet/examples/pretender.toml`, wq#3850,
> version "0", unpinned) — this is the go/no-go that toml's own header says must happen
> before any promotion past that tier.
> ...
> Leave `limpet/examples/pretender.toml` at community tier, version "0", unpromoted.

This is fabricated. Checked from three independent angles, all in this repo (no access
needed to anything outside the verify scope):

1. **The file does not exist.** `find /home/lumen/foreman -iname "*pretender*"` returns
   nothing under `limpet/`. `limpet/examples/` contains exactly three files —
   `ghidra.toml`, `ghidra-mcp.toml`, `hackrf.toml` — generic plugin-manifest *templates*
   (placeholder git URLs like `https://github.com/example/limpet-hackrf`), not a
   per-tool catalog with tier/version state for real entries.
2. **No "tier" concept exists anywhere else.** `grep -n -i tier TOOL_REGISTRY.md`
   returns only the three lines quoted above — all inside wq#3877's own section. A
   repo-wide grep for `catalog tier`/`"tier"`/`community tier` across `.md/.json/.toml/.py`
   turns up no other hit in `limpet/` or its registry/schema code.
   `limpet/registry/schema.py`, `limpet/registry/tools.d/*.yaml` (the real, in-use
   per-tool manifest format — see `ghidra.yaml`: `name/path/version/category/...`,
   no tier field) and the catalog JSON fixture
   `limpet/registry/tests/fixtures/tool-catalog.sample.json` (`name/version/category/...`
   or `name/ver/group/...`, again no tier field) confirm the real schema has no such
   gating mechanism at all.
3. **wq#3850 — the ticket that actually touched Pretender's catalog entry, same day,
   ~2 minutes before wq#3877 claimed — directly contradicts this.** Its done-notes
   (`wq show 3850`) state the real catalog lives at
   `/home/lumen/projects/limpet/tool-catalog.json` (JSON, not a toml file, and not under
   `limpet/examples/`) and explicitly: *"per-tool plugin manifests (limpet-plugin.toml)
   - need verified upstream version/install/health per tool and live in per-plugin git
   repos per docs/plugin-spec.md; none exist for any existing catalog entry, so not
   fabricated here."* I.e. as of the sibling ticket's own close, zero per-tool manifest
   tomls existed for **any** catalog entry, Pretender included — not "version 0", not
   any version.

So the doc invents a specific file path, a version number, a "community tier," and a
governance mechanism ("the go/no-go that toml's own header says must happen") that none
of exist, and asserts it as the reason the evaluation was commissioned and as the
disposition to leave things in. This is exactly the "never guess a path/config
value — verify it" failure the doctrine warns about, landed inside a doc whose other
four citations were all genuinely checked against live sources. It reads as a plausible
process-governance detail stitched together from real fragments nearby (the real
`limpet/examples/*.toml` path convention, hackrf.toml's real `maintainer = "community"`
field, the real wq#3850 ticket number) without checking whether the combination was true.

## Why this doesn't flip the headline verdict

The actual go/no-go reasoning (fragile hardware-in-the-loop rig, frozen 2019 toolchain,
no Qiling/Unicorn adapter, Conware-documented overfitting to the recorded trace) is
sound and independently reproduced above, and doesn't depend on the fabricated
tier/file sentence at all — that sentence could be deleted outright and the verdict
would be unaffected. This is a documentation-integrity defect, not a wrong
recommendation. Treating it as the former: CONTRADICTION on the specific claim,
CONFIRM on the substantive research.

## Follow-up

Filed wq#3889 (fix: strip/replace the fabricated `limpet/examples/pretender.toml`
tier/version/governance claim in `TOOL_REGISTRY.md:94-96,139` with accurate language
tied to the real `tool-catalog.json` entry from wq#3850) — small, mechanical, doesn't
need re-opening wq#3877 itself.
