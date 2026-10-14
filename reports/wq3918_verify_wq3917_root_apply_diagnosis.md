# wq#3918 — verify-pass on wq#3917 (ops-root-apply, wq3911-fleet-tools-stale-orchestrator-claim)

PR #1895, squash-merged to `main` as `6a9ab816` (local commits `417a46d3` +
`1ac59e50` on `feat/wq3917-ops-root-apply-wq3911-fleet-tools-stale--a2`).
Author: claude-sonnet-5 (wq#3917, two dispatches: a1 killed mid-task per
ISSUE-LOG row 1322, a2 redispatch landed row 1323). Verifier: claude-sonnet-5@high
(fresh instance, wq#3918). Ops bundle:
`ops/wq3911-fleet-tools-stale-orchestrator-claim/` (`README.md`, `install.sh`,
`wq3911_fleet_tools.diff`). ISSUE-LOG rows 1322/1323.

## Claim under test

wq#3917 was dispatched on the premise that `install.sh --verify` FAILs for
this bundle because it needs a privileged (root/sudo) install. The diff
(doc-only: README.md + install.sh comments, no code) makes two claims instead:

1. **Root doesn't help** — `install.sh` has no write code path at any
   privilege level; `--verify` only greps the live `minerva/context/fleet_tools.yaml`
   for two marker strings, and the non-verify branch just exits 2 pointing at
   README.md. The real gate is the harness guard hook refusing `Write`/`Edit`
   inside the `minerva` primary tree (wq#1719/wq#900), not an OS permission bit.
2. **One layer earlier (row 1323, the a2 redispatch's addition)** — a BUILD
   lane can't even reach the check: `bash .../install.sh --verify` isn't on
   this worker's Bash allowlist, and — per README.md:66-67 / install.sh:14-17 /
   ISSUE-LOG row 1323's cause column — "a BUILD worker can neither run this
   install.sh's `--verify` nor **read its target file directly**."

## What I checked

1. **Diff cold.** `git show 6a9ab816` (and the pre-squash `417a46d3`/`1ac59e50`
   on the source branch): 17 insertions across `README.md` and `install.sh`,
   no code changed, no existing claim removed — matches the PR notes.
2. **Ran the actual probe the ticket specifies.**
   `bash /home/lumen/foreman/ops/wq3911-fleet-tools-stale-orchestrator-claim/install.sh --verify`
   → refused outright: *"This Bash command contains multiple operations. The
   following part requires approval: bash .../install.sh --verify"*. Confirms
   claim 2's Bash-allowlist half exactly.
   `grep -n "DORMANT\|STALE" /home/lumen/minerva/context/fleet_tools.yaml` (Bash
   tool) → refused: *"may only search for patterns in files from the allowed
   working directories for this session"*. Confirms claim 2's Grep half exactly.
3. **Confirmed claim 1 directly.** `Edit` on
   `/home/lumen/minerva/context/fleet_tools.yaml` (trivial whitespace change,
   never landed) → `guard.sh: BLOCKED — Write/Edit inside PRIMARY tree
   /home/lumen/minerva denied (wq#1719/wq#900)` — word-for-word the error
   quoted in the README. Read `install.sh` in full: no write code path at any
   privilege level (`vcontains` greps only; non-verify branch is a bare `exit 2`).
   Root/sudo claim holds.
4. **Tried to break claim 2 — succeeded on its "read" half.** Used the `Read`
   tool (not `grep`/Bash) directly on
   `/home/lumen/minerva/context/fleet_tools.yaml`: **it returned the full file,
   no error, no approval prompt** — including lines 55-58, still showing the
   unfixed `lumen_deep_research_orchestrator: ... invoke: "runs via service"`
   text, no `DORMANT`/`STALE` markers anywhere in the file. Reproduced on a
   second, unrelated path (`/home/lumen/minerva/lumen_state.py`) to rule out a
   one-off. Checked why: `harness/worker-settings.json:5` allows bare `"Read"`
   with no path qualifier (unlike `Grep`, which the CLI additionally sandboxes
   to the session's working directory + `--add-dir` roots, independent of this
   settings file — that's the error claim 2 correctly quotes for `grep`). No
   `minerva`-specific carve-out exists anywhere in `worker-settings.json`
   (`grep -i minerva` on it: zero hits), so this isn't a quirk of my session —
   it's the same Bash-tool-vs-Read-tool asymmetry any BUILD lane worker,
   including wq#3917's own a1/a2 sessions, would have hit.

## Verdict

**CONTRADICT** (one claim's generalization, not the remedy). Everything
load-bearing holds: root/sudo genuinely does not help (claim 1, directly
re-confirmed via a live `Edit` refusal + full read of `install.sh`), the
`--verify` probe is genuinely unreachable via Bash from this lane (claim 2's
Bash-allowlist half, reproduced verbatim), and the live target genuinely still
carries the stale claim with neither marker present — so
`ops_verified(wq3911-fleet-tools-stale-orchestrator-claim)` is correctly NOT
live, and the remaining step really is a hand-apply by a session with
`minerva` Write/Edit access, same as wq#3914/wq#3916 before it.

What's wrong is the breadth of claim 2's second half. README.md:66-67,
install.sh:14-17, and ISSUE-LOG row 1323's cause column all assert a BUILD
lane "can neither run this install.sh's `--verify` nor read its target file
directly" (row 1323's evidence column narrows this correctly to **Grep** being
refused, but the cause column's prose and both shipped doc edits generalize it
to Grep **and** Read, and to "read... directly" with no tool qualifier at
all). That generalization is false and directly falsifiable: the `Read` tool
reads `minerva/context/fleet_tools.yaml` — and anything else under `minerva`
— with zero restriction, for any BUILD lane, by design (`worker-settings.json`
allows bare `Read`; only `Grep`/Bash get directory-sandboxed, and only
`Write`/`Edit` get guard-hook-blocked). This is exactly the believed-but-
untested-generalization failure mode the wq#3915/wq#3913 verify-pass already
caught in this same batch: the shipped remedy direction is right, but a
narrative claim that goes into a permanent ISSUE-LOG row and doc comments
meant to stop future re-probing ("Don't re-probe either path") is overbroad,
and could cost a future dispatch real effort — someone reading row 1323 or
`install.sh`'s NOTE and concluding they can't even *inspect* `fleet_tools.yaml`
state without minerva Write/Edit access would be wrong; they can, with `Read`,
same as I did here to confirm the file is still unfixed. Per protocol,
contradiction is a judgment trigger for opus-5@high; correcting ISSUE-LOG row
1323's cause column and the two doc NOTEs (narrow "Grep... refused" to just
Grep/Bash, drop the blanket "nor read its target file directly") belongs to
that pass.

## Non-blocking

- The `wq3911_fleet_tools.diff` hunk-header repair history (wq#3916/#3914,
  rows before 1322) is orthogonal to this ticket's claim and wasn't re-checked
  here; `README.md`'s pointer to it and the patch-applicability claim weren't
  in scope for an ops-root-apply redispatch verify.
