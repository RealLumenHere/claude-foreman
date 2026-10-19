# wq#4318 — Publish Spectrio S905X3 Armbian project to GitHub

## What shipped

`projects/spectrio-armbian/`: README, DEVICE_QUIRKS.md, BOOT_ARGS.md,
FLASH_PROCEDURE.md, EMMC_INSTALL.md, and `provision.sh` — a self-contained
writeup of the Spectrio S905X3 Armbian bring-up (SM1 SoC, PEF7071 Ethernet
PHY fix, `mem=3552M` boot-arg, eMMC install path), ready to become the
content of a standalone public repo once the two follow-ups below land.

Content is sourced from, and verified against, two places:

1. The ticket's own description (Frank, wq#4318) — the `mem=3552M` figure
   and the high-level list of what the bring-up covers are taken as given,
   not re-derived.
2. The fleet's own existing internal writeup of this same bring-up, read
   (not edited) from outside this worktree:
   `projects/spectrio-tf1/golden/{meson-sm1-spectrio-tf1.dts,provision.sh}`
   and `projects/spectrio-tf1/census/CENSUS_METHODOLOGY.md` (all under
   `/home/lumen/projects/spectrio-tf1/`, the fleet-shared, non-git scratch
   tree the "TF1" appliance tickets — wq#3429/wq#3430/wq#4181, all already
   merged to main in this repo — cite as where the proven image/DTB live).
   Plus one public corroborating source for the PHY identity (an LKML
   thread confirming PEF7071 is a real part in the `intel-xway` driver
   family).

## Why this isn't a finished, pushed public repo yet

Two things this worker's dispatch does not have access to, both recorded
rather than guessed around:

1. **The actual binary/complete artifacts** (full `.dts`/`.dtb`, exact
   per-unit flash transcripts) live on "maksoor", the fleet's RE
   workstation, not in any git-tracked path this worktree can reach.
   `fleet-exec run maksoor` → `DENY [grant:grant_missing_env]`: this
   ticket's `needs:` field was empty at dispatch, so
   `fworker.resolve_exec_grant` minted no grant — the same root cause
   already on record at `kb/ISSUE-LOG.md` row 192 (wq#1444). The fix there
   was "file a follow-up with the right `needs:` tag"; done here as
   wq#4322 (`needs:ssh(maksoor)`).
2. **Actually creating a public GitHub repo and pushing to it** needs a
   `gh repo create`/push-capable credential. This worker's `gh` allowlist
   (`harness/worker-settings.json`) only permits read subcommands
   (`pr/issue/run view|list|diff|checks`) — there is no repo-create or push
   credential provisioned to a build worker, and minting/using one on
   Frank's GitHub account is exactly the physical-task exception in
   instinct 3. Filed as wq#4324, `frank_gated`, `--no-probe` (no
   deterministic check exists for "did Frank authorize this credential").

Neither blocks landing what *is* real and complete here.

## Deps / follow-ups

- wq#4322 — ran with `needs:ssh(maksoor)`; the grant worked (ISSUE-LOG
  #192/wq#1444's fix holds) but an exhaustive search of maksoor found none
  of the claimed golden `.dts`/transcripts — they live in a lumen-local
  non-git scratch tree instead (`kb/ISSUE-LOG.md` row 1457). Superseded by
  wq#4326 (`needs:dir(/data/lumen/projects/spectrio-tf1-cluster)`), which
  replaces this repo's TODO markers with the verified complete content.
- wq#4324 — Frank-gated: create the public repo, push, pick a license.

No Thor restarts; nothing here touches Thor.
