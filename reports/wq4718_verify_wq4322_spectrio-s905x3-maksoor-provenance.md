# Verify-pass: wq#4718 on wq#4322 (PR #2504)

- **Target**: PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/2504, single commit `876f28ce`, merged to `main` via merge commit `ce03f93e`.
- **Author model**: claude-sonnet-5 (medium effort). **Verifier**: claude-sonnet-5@high, fresh instance.
- **Diff scope**: 12-line addition to `projects/spectrio-armbian/DEVICE_QUIRKS.md` — a "Provenance re-check (wq#4322)" section.

## Claim under test

wq#4322 was dispatched to pull 4 artifacts from maksoor (golden `.dts`/`.dtb` with the full
ethernet-phy node, a per-unit flash/eMMC-install transcript, confirmation of the `mem=3552M`
root cause, and landing it all in `projects/spectrio-armbian/`). The PR's claim: all of that was
already landed by an earlier ticket (wq#4326, commit `a3acf112`) before wq#4322 was even
dispatched, and a direct `ssh`/`fleet-exec run maksoor` check of maksoor's home tree found no
Spectrio/S905X3/TF1 artifacts — maksoor's current working set is unrelated hardware. Conclusion:
nothing further to pull.

## Method

- No MCP access to `pull_request_read` was granted mid-session (permission denied twice); rather
  than retry, fetched the PR head directly: `git -C worktree fetch origin
  refs/pull/2504/head:pr-2504-verify` — single commit, identical to the merged `876f28ce`.
- `git merge-base --is-ancestor` to confirm merge state and ordering, instead of trusting prose.
- Read the landed `.dts` directly for the ethernet-phy node content.
- Read `README.md`/`BOOT_ARGS.md` for the pre-existing (wq#4326) per-unit-transcript disclosure.
- Tried to re-run the PR's own probe: `fleet-exec run maksoor "echo probe-test"` → `DENY
  [grant:no_ssh_run]: no ssh grant for maksoor with verb run`. This ticket's `needs` is
  `ssh(anubis)` (anubis = the Gitea/Borg host at 100.121.17.5, confirmed via
  `lumen_state.py current anubis`), not `ssh(maksoor)` — the dispatcher scoped this verify-pass
  to reviewing the Gitea-hosted diff, not re-running wq#4322's maksoor filesystem probe.

## Findings

1. **Provenance ordering holds.** `a3acf112` (wq#4326, landed 2026-10-19 00:48:27) is a confirmed
   git ancestor of `876f28ce` (2026-10-27 07:00:00), and `wq#4322`'s `claimed_at` is 2026-10-27
   06:56:46 — the artifacts genuinely predate this ticket's dispatch. Not a backfilled claim.
2. **DTS content matches the corrected node, not the ticket's original (wrong) name.**
   `projects/spectrio-armbian/meson-sm1-spectrio-tf1.dts:1938` has `ethernet-phy@0 { reg =
   <0x05>; ... }` — matching wq#4326's own correction ("real PHY node... not @5"). The PR diff
   text doesn't restate this detail, so it isn't exposed to the error; only wq#4322's `done`
   notes (ticket metadata, not the shipped diff) still say "ethernet-phy@5," a stale name carried
   over from the original wq#4318 ticket text. Cosmetic, not a defect in what shipped.
3. **"Per-unit transcript" is honestly absent, and the PR doesn't claim otherwise.**
   `README.md:56` already states "Per-unit transcripts: none exist" (from wq#4326, pre-dating
   this PR). wq#4322's own `done` notes gloss this as "eMMC install procedure" landed — a
   different, weaker artifact than the "exact per-unit flash/eMMC-install transcript" the
   original ticket asked for. That's an overclaim, but it's in the ticket's bookkeeping field,
   not in the 12-line diff itself: the diff's own wording is narrower and accurate ("whether
   maksoor holds additional per-unit flash transcripts beyond what's here... found no
   [artifacts]"), and doesn't assert the transcript exists anywhere.
4. **The one unverifiable leaf: the live maksoor filesystem check.** Could not be independently
   re-run under this ticket's grant (`ssh(anubis)` only; `fleet-exec run maksoor` denies with
   `no_ssh_run`). No evidence surfaced disputing it either. Flagging as a verification limit, not
   a contradiction — re-probing maksoor was outside this ticket's resolved capability by design.

## Verdict: CONFIRM

The shipped diff's claims are supported by git history and repo content; the real gap (stale
"ethernet-phy@5" naming, and the "per-unit transcript" vs. "eMMC install procedure" conflation)
lives in wq#4322's `done`-note prose, not in the 12 lines that actually shipped. No grounds to
contradict and escalate to opus-5@high.

## Residual note (not a contradiction, for the board)

wq#4322's `done` notes overclaim completeness by substituting "eMMC install procedure" for the
originally-requested "per-unit flash/eMMC-install transcript," which `README.md` itself documents
as absent. Future verify-passes on tickets with a similar "already landed elsewhere" shape should
diff the *original ask* against the *done-note's own enumeration* word-for-word, not just against
the shipped diff — the gap here was in the bookkeeping, not the content.
