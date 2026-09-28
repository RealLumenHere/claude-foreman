# wq#2947: judgment on wq#2940's CONTRADICT of wq#2908's branch-guard exemption

Escalated from wq#2940's verify-pass (claude-sonnet-5@high) of wq#2908/PR#166
(lumen-bin@2b78fc3). Written 2026-09-28 by opus-5@high under the archetype-8 escalation rule.
Every fact below was re-derived from the repo and from probes run here, not carried over from
wq#2940's report.

## Ruling: CONTRADICT upheld. Option (a) — revert. Landed as lumen-bin@fc6aa16abd (PR#168).

wq#2908's item 2 (mirror push from a bare clone under `/home/lumen/mirrors`, never from a
primary working tree) fixes the blocked-push root cause by itself. Item 1 — the
`GIT_MIRROR_SYNC=1` early exit at the top of `.githooks/branch-guard`'s `cmd_push` — bought
nothing and cost an invariant. It is gone, along with the env var on `github-mirror-sync`'s
push and the comment that documented it.

## Why (a) and not (b)

Option (b) was to keep the exemption but scope it to the `github` remote's name and URL. I
rejected it. Scoping would put an env override into a hook whose header promises
*"Deterministic, no LLM, no env override"* (`.githooks/branch-guard:12`, the line wq#1476
added after three straight-to-main incidents — wq#792, wq#794, wq#927) in order to guard a code
path that has no hook to be exempted from. Defense-in-depth is a reason to keep live code, not
a reason to keep dead code. The failure mode (b) hedges against — someone installs the guard
into a mirror clone — is fail-safe: the push aborts, wq#2908's item 3 files an `aq` row, and the
correct repair is to unguard the mirror clone. That instruction now lives in
`github-mirror-sync`'s header instead of a bypass in `cmd_push`.

## What I re-verified independently

| Claim | Status | Evidence |
|---|---|---|
| the exemption is at the top of `cmd_push`, before remote/ref are read | confirmed | `main:.githooks/branch-guard:76-98`: `root=$(repo_root .)`, then the `GIT_MIRROR_SYNC` block at `:79-86`, then `def=$(default_branch …)` at `:87` and the `while read … remote_ref` loop at `:88`. `cmd_push` never reads `$1`/`$2` at all |
| the hook's own header forbids exactly this | confirmed | `:12` "Deterministic, no LLM, no env override", unchanged by 2b78fc3 (`git show 2b78fc3` touches only `cmd_push` and the new script) |
| only the script's inline push sets the var | confirmed | `grep -rIl 'GIT_MIRROR_SYNC=1'` over `/home/lumen/{bin,foreman,agents/work-queue,mailbot,projects/nest}` matched exactly one file, wq#2940's own report. `main:github-mirror-sync:65` was the only setter in code |
| the bypass is real, not theoretical | **confirmed, and stronger than wq#2940 claimed** | negative control below: with the exemption present, `GIT_MIRROR_SYNC=1 git push origin fix/thing:main` **moved the fixture's `origin/main`** |
| a `--mirror` clone of a guarded repo is hookless | confirmed | new test case (xii): clone `--mirror` of a repo with the guard installed → 0 non-`*.sample` files in `hooks/`, `core.hooksPath` unset, and `push github --mirror` replicates `main` with no env var set |
| "the dead branch is installed on every guarded primary" (wq#2940 §3) | **overstated — see below** | the installed copy is a snapshot taken at install time, not a live read of the tracked file |

## One correction to wq#2940: the footgun was latent, not live

wq#2940 §3 says the dead branch "lives in the tracked `.githooks/branch-guard`, which **is**
installed on every guarded primary (lumen-bin, claude-foreman, minerva, fleet-inventory,
mission-control, ccc)". Two things are wrong with that.

`cmd_install` copies the file (`.githooks/branch-guard:161`,
`write_file "$hooks/branch-guard" < "$SRC_DIR/branch-guard"`); it does not symlink or re-read it.
So the installed guard is whatever was current when install last ran:
`grep -c GIT_MIRROR_SYNC /home/lumen/bin/.git/hooks/branch-guard` → **0**. The live guard never
carried the exemption.

And claude-foreman is not guarded at all: `git -C /home/lumen/foreman config --get
core.hooksPath` exits 1 and `/home/lumen/foreman/.git/hooks` holds only `*.sample`. The six repos
in that list are `github-mirror-sync`'s `REPOS` map (mirror targets), not branch-guard's install
set. branch-guard is lumen-bin's file; the only tree it is installed into is `/home/lumen/bin`.

This does not change the verdict — it sharpens it. The exemption would have gone live on the
next `branch-guard install` from a main-based checkout, which is exactly what redeploying the
guard does. It was a loaded gun with the safety on, not a fired one. The verdict stands because
"it hasn't gone off yet" is not a defence for a bypass of a documented no-override invariant.

## The probe, and the negative control that makes it falsifiable

wq#2908's own verification ("branch-guard refuses/exempts/refuses across no-env/1/0") could not
have caught this: it exercised the exemption from inside the population it was designed for. The
new cases in `tests/test_branch_guard.sh` probe from outside it — remote `origin`, not the
`github` mirror — which is where the bypass actually bites.

Three runs of `tests/test_branch_guard.sh`:

| tree | result |
|---|---|
| fix branch (`fc6aa16`) | **63 passed, 0 failed**, exit 0 |
| unfixed `main` + the new cases (negative control, worktree `/tmp/fworker-wq2947/neg` at `82ce01e`) | 48 passed, **11 failed** |
| unfixed `main`, stock test (baseline, same worktree) | 48 passed, 1 failed |

The negative control is the point: the new cases fail on unfixed code, so a green run on the fix
branch means something. Its most telling line is `FAIL origin main moved under
GIT_MIRROR_SYNC=1` — the bypass did not merely skip the check, it let a feature branch land on
the fixture's `origin/main`, and the four cascading failures after it are later cases tripping
over a `main` that had already moved.

## Incidental find: a test assertion that could not pass

The 1 baseline failure is not related to wq#2908. `tests/test_branch_guard.sh` case (iii) grepped
`repo_sweep.py --dry-run` output for `would file`, but `harness/repo_sweep.py:307` only ever emits
`[dry run — would record]` — the string `would file` appears nowhere in that script, so the
assertion could not match on any input and the suite has been exiting 1. Fixed to `would record`
in the same PR. Without it the suite never goes green, so a green run could not have gated the new
cases. Same class as the finding it accompanies: a check that cannot fail is worth as little as a
check that cannot pass.

## Still open (not gates, not mine to run)

- `/home/lumen/bin`'s primary tree is parked on `fix/wq2908-github-mirror-sync`, so the landed
  revert is not in the live working tree yet (ISSUE-LOG row 920). `lumen-pr land` says so itself
  and it is harmless here — the live installed guard at `/home/lumen/bin/.git/hooks/branch-guard`
  never had the exemption. Repair is one operator command a sandboxed worker cannot run:
  `git -C /home/lumen/bin checkout main && lumen-pr ffprimary /home/lumen/bin`.
- wq#2908 item 5 (live `github`==`origin` equality check in `lumen-repo-sweep`) is still deferred,
  as wq#2908's own done-notes disclose. Out of scope for this judgment.
