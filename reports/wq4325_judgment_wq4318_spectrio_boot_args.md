# wq#4325 — verify-pass of wq#4318 (PR #2222) — CONTRADICT, escalated

**Verdict: CONTRADICT (localized).** wq#4318's overall framing is honest — it clearly
discloses that the real DTB/driver artifacts and the public-repo push are still pending
(wq#4322/wq#4324), and every internal/external citation I checked holds up. But
`BOOT_ARGS.md`'s "Where it goes" section makes a specific, checkable technical claim
that is false, about the one feature the ticket explicitly asked for ("boot args"): it
asserts other shipped docs demonstrate where `mem=3552M` gets applied, and they don't,
and it names the wrong config variable for the actual target image.

## What I verified and confirmed clean

Read PR #2222 cold (`git show --stat 340850ca` / `4eb1f756` — same tree, different SHA;
see "Bookkeeping note" below) and checked every citation in
`projects/spectrio-armbian/{README,DEVICE_QUIRKS,BOOT_ARGS,FLASH_PROCEDURE,EMMC_INSTALL}.md`
and `provision.sh` against its claimed source, not just against the author's description:

- `limpet/target-devices.d/amlogic.toml` (this worktree): VID:PID `1b8e:c003`,
  `soc_family`/`soc_model` list, and the `boot.py` vs `boot-g12.py` split — all match
  `DEVICE_QUIRKS.md`'s "Identity" section verbatim.
- `docs/wq3253_ewf_amlogic_allwinner_backends.md:73` (this worktree): confirms
  `boot-g12.py` "can only push a `u-boot.bin`" for G12A/G12B/SM1 — matches
  `DEVICE_QUIRKS.md` and `FLASH_PROCEDURE.md` step 4 exactly.
- `/home/lumen/projects/spectrio-tf1/golden/meson-sm1-spectrio-tf1.dts` and
  `provision.sh`, `/home/lumen/projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md`
  (fleet-shared, outside this worktree, read-only): the reserved-memory layout (3 MiB +
  32 MiB secmon, 256 MiB CMA), the `ethernet@ff3f0000` MAC node body, the "PEF7071 @
  MDIO 5, rgmii, no rx-delay" annotation (present verbatim as a comment in
  `provision.sh:9` and in `CENSUS_METHODOLOGY.md:66`), and the eMMC partition layout
  (117 MiB/629 MiB, `-110` voltage-select failure on mainline U-Boot) — all match. The
  golden `.dts` genuinely has no `ethernet-phy@5` child node under `mdio {}` (it's a
  `dtc`-decompiled stub), and `DEVICE_QUIRKS.md` already discloses exactly that gap
  rather than inventing the node body — this is a correctly-hedged claim, not a defect.
- LKML citation (`lkml.org/lkml/2026/9/22/1668`, Andrew Lunn, intel-xway 100BASE-TX
  workaround) — fetched via `fleet-search web`, real thread, text matches including the
  "GPY111/PEF7071/PHY11G" grouping cited in `DEVICE_QUIRKS.md`.
- `mem=3552M` — confirmed present verbatim in Frank's own wq#4318 ticket text
  (`wq show 4318`), so "carried over from the ticket, not re-derived" is accurate framing,
  not a cover for a guess.
- Follow-up tickets wq#4322 (`needs:ssh(maksoor)`, claimed) and wq#4324
  (`frank_gated`/`gate_type: frank_decision`, `[NO-PROBE: ...]`) exist with content
  matching what the shipped README claims about them.
- `gh` allowlist in `harness/worker-settings.json` is read-only
  (`pr/issue/run view|list|diff|checks`) — the "no repo-create/push credential" claim
  holds; nothing was pushed to a real public GitHub repo.

## The contradiction

`BOOT_ARGS.md` → "Where it goes":

> Same place any ophub `s905x3` boot-arg override goes: the `APPEND=` (or
> equivalent) line in the image's `armbianEnv.txt` on the boot partition... .
> `FLASH_PROCEDURE.md` and `provision.sh` show where this sits relative to the
> DTB/overlay changes.

Both halves of this are wrong, checked two ways:

1. **The cross-reference is false.** `FLASH_PROCEDURE.md` step 3 enumerates
   `provision.sh`'s actions and says outright "it's short and this is the complete
   list of what it touches": copy the `.dtb`, copy `u-boot-x96maxplus.bin` to
   `u-boot.ext`, point `FDT=` at the copied DTB. `provision.sh` (both the published copy
   and the fleet's golden original at `/home/lumen/projects/spectrio-tf1/golden/`) only
   runs a `cp`/`cp`/`sed` on `FDT=`. Neither file sets, mentions, or shows where
   `mem=3552M` (or any `bootargs`/`extraargs`/`APPEND=` line) goes. A reader who follows
   `FLASH_PROCEDURE.md` + `provision.sh` end to end gets the DTB overlay applied and
   **no memory-cap boot arg anywhere** — the one kernel-command-line fix this entire
   bring-up is named for in the ticket title never actually gets applied by the
   documented procedure.
2. **The named variable is wrong for the actual target.** This repo documents the
   `ophub/amlogic-s9xxx-armbian` image specifically (`README.md` → "Base image"). That
   image's `armbianEnv.txt` uses `extraargs=` for extra kernel command-line parameters
   (confirmed via `fleet-search web "ophub amlogic-s9xxx-armbian armbianEnv.txt
   extraargs"` — e.g. `extraargs=earlycon rootflags=... net.ifnames=0`), not `APPEND=`.
   `APPEND=` is a generic/guessed syllable, not a value the search surfaces for this
   image family. This is exactly the kind of guess the ticket's own research-first
   doctrine (`fleet-search web` before reasoning from inside the problem) is supposed to
   catch, and the same PR *does* apply that doctrine correctly everywhere else (the LKML
   cite, the pyamlboot cite) — this one claim skipped it.

## Why this is acceptance-relevant, not cosmetic

wq#4318's own ticket text lists "boot args" as one of five things this publish must
include. The shipped `BOOT_ARGS.md` doesn't just under-specify the mechanism (which
would be an honestly-hedged gap, like the DTB `ethernet-phy@5` node body or the
root-cause kernel log, both of which this PR correctly flags as pending) — it asserts,
with a confident cross-reference to two other files in the same repo, that the mechanism
*is* shown, and names a concretely wrong variable while doing it. A community reader
hits a dead end exactly where the doc tells them to expect an answer. That is a
correctness defect in the one deliverable most central to the ticket's title, not a
disclosed limitation.

## Not re-litigated

- `EMMC_INSTALL.md`'s partition layout and voltage-select claim — checked against
  `CENSUS_METHODOLOGY.md`, matches exactly, no issue.
- The missing `.dtb` binary in the published `provision.sh`'s working directory — already
  disclosed in `README.md` → "Status" and `FLASH_PROCEDURE.md` step 3's own note; not a
  hidden gap.
- Commit-hash bookkeeping: wq#4318's done-notes cite merge SHA `4eb1f756c2`, which is on
  branch `feat/wq4318-...` (not reachable from `origin/main`); the content actually on
  `main` is `340850ca`, a rebase of the identical tree onto a newer main tip (`git diff
  4eb1f756 340850ca -- projects/spectrio-armbian ops/wq4318-*` is empty). Same content,
  different SHA from a rebase-at-merge — not a discrepancy in what shipped, not flagging
  as a finding.

## Disposition

Localized, checkable, acceptance-relevant defect in a merged PR — escalating per
archetype 8 to opus-5@high judgment on disposition (reopen wq#4318 vs. forward-fix
`BOOT_ARGS.md` in place, same choice wq#4291 faced for wq#4271). My own read: this looks
like a forward-fix (swap `APPEND=` for `extraargs=`, and replace the false
`FLASH_PROCEDURE.md`/`provision.sh` cross-reference with an honest statement that no
file in this repo yet demonstrates applying the memory boot-arg — add it as a fourth,
disclosed gap alongside the DTB node body and the root-cause kernel log, all three
already queued for the maksoor pull in wq#4322) rather than a reopen-and-resurvey, since
the rest of the PR verifies clean. Final call left to the judgment pass. Filed as
wq#4327 (deps wq#4318, wq#4325).
