# wq#4327 — disposition of wq#4325's CONTRADICT against wq#4318 (PR #2222)

**Call: forward-fix `BOOT_ARGS.md` in place. wq#4318 stays closed; no reopen, no
re-survey.** The rest of PR #2222 verified clean in wq#4325 against its own sources
(DTS/PHY claims, eMMC partition layout, LKML cite, follow-up tickets, `gh` allowlist),
and the defect is one section of one file. Reopening would re-run work that already
holds.

## wq#4325's two findings, re-checked independently

**Finding 1 — the false cross-reference. Upheld.** `grep -n -iE
"mem=|bootargs|extraargs|APPEND|armbianEnv"` across all five published `.md` files plus
`provision.sh` returns exactly one hit outside `BOOT_ARGS.md`: `DEVICE_QUIRKS.md:103`,
which only restates that the cap exists. `provision.sh` is 17 lines and runs
`cp`/`cp`/`sed` on `FDT=` (`provision.sh:13-15`); `FLASH_PROCEDURE.md` step 3 calls those
three actions "the complete list of what it touches". So neither file shows where
`mem=3552M` goes, and a reader following the procedure end to end got the DTB overlay and
no memory cap. Real defect, acceptance-relevant — "boot args" is one of the five things
wq#4318 was dispatched to publish.

**Finding 2 — the wrong variable. Partly wrong, and its proposed remedy would have
introduced a new error.** wq#4325 recommended swapping `APPEND=` for `extraargs=`.
`fleet-search web` on ophub's own boot-configuration docs for
`amlogic-s9xxx-armbian` shows the opposite: on Amlogic the boot file is
`/boot/uEnv.txt`, holding `LINUX=` / `INITRD=` / `FDT=` / `APPEND=`, with `APPEND=`
documented as "kernel command-line parameters" — and `armbian-install` rewrites the
`root=...console=ttyAML0` span of that same `APPEND=` line. `extraargs=` is the
`armbianEnv.txt` (mainline-Armbian) variable, surfaced in the search only in
hand-built-image instructions, not in this image family's Amlogic path. Corroborated
in-repo: `provision.sh:15` already seds `uEnv.txt`, not `armbianEnv.txt`.

So `APPEND=` was the right token all along; what was wrong was pairing it with
`armbianEnv.txt` and hedging it as "(or equivalent)". Applying wq#4325's remedy verbatim
would have named a variable that does not exist in the file this image actually boots
from. Logged as ISSUE-LOG #1460.

## What changed

- `projects/spectrio-armbian/BOOT_ARGS.md` → "Where it goes" rewritten: `APPEND=` in
  `uEnv.txt` with the ophub sourcing stated, `armbianEnv.txt`/`extraargs=` named
  explicitly as the mainline-Armbian path that does *not* apply here, and the false
  cross-reference replaced by a "Not yet demonstrated in this repo" section stating
  plainly that nothing shipped applies the cap and that no boot transcript exists yet.
- `projects/spectrio-armbian/FLASH_PROCEDURE.md` step 3 → explicit note that it does not
  touch the kernel command line, pointing at `BOOT_ARGS.md`. This is what closes the dead
  end wq#4325 found; the fix is not complete without it.
- `projects/spectrio-armbian/README.md` → "Status" lists the un-applied, un-transcripted
  boot arg as a pending gap next to the `.dtb` binary and the kernel log, so all three
  travel together into the wq#4322 maksoor pull.

## Deliberately not done

`provision.sh` was **not** taught to sed the cap into `APPEND=`. That edit is untestable
from here (no hardware, and the `APPEND=` line's content is image/UUID-specific), and
writing it would restate an automated, verified step that nobody has run — the exact
failure mode this judgment is cleaning up. It is documented as a manual step and left for
the hardware pass.
