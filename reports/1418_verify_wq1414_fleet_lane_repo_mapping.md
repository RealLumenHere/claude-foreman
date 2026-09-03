# wq#1418 — Adversarial verify-pass of wq#1414 (fleet lane: map LANE_REPO or retire)

**Verdict: CONTRADICT** — the mechanical fix (adding `"fleet": FOREMAN_ROOT` to
`LANE_REPO`) does eliminate the `lane_unmapped` skip for the fleet lane, and the
`evidence.json` citation (`orchestrator/router.py:100`) is accurate. But the PR's
per-group judgment call — "all 4 open fleet tickets (1296/1297/1133/1373) are
foreman-repo deliverables" — is factually wrong for at least wq#1133, whose own
(unambiguous) detail names a different repo. Under the shipped mapping, dispatching
wq#1133 hands a worker a `foreman`-rooted worktree for a ticket whose deliverable is a
script that must live in `lumen-bin`, and the guard rails then block the only path to
actually land it. This is the exact wrong-repo-dispatch class wq#1414's own ticket text
was trying to avoid ("map to a real repo via LANE_REPO **or needs:repo tags**") — it
just moved the failure from a loud skip to a silent, worse one.

## What was shipped

PR http://100.121.17.5:3000/lumen/claude-foreman/pulls/285 (merged, commit `6d332bc` /
`cba73bb`, 2 files): added `"fleet": FOREMAN_ROOT` to `LANE_REPO`
(`orchestrator/router.py:100`) with a comment claiming the fix covers "the 4 open fleet
tickets at ticket time (wq#1296/1297/1133/1373) — all foreman-repo deliverables."

## Mechanics — CONFIRMED

Built a detached worktree at the merge commit (`git worktree add ... 6d332bc --detach`)
and ran the actual shipped resolver against the 4 live fleet tickets:

```
router.repo_for_ticket(1296) -> /home/lumen/foreman
router.repo_for_ticket(1297) -> /home/lumen/foreman
router.repo_for_ticket(1133) -> /home/lumen/foreman
router.repo_for_ticket(1373) -> /home/lumen/foreman
```

All 4 now resolve (no more `lane_unmapped`), matching the ticket's stated "Done" bar.
`repo_mismatch()` doesn't catch any of them either — it only fires on a leading
`[tag]`/first-word title match against `TITLE_REPO_MARKERS`, and none of these titles
carry one.

## Bug — wq#1133 targets the wrong repo

wq#1133's detail (status: open, `needs:` empty, so nothing overrides the lane default)
ends with an explicit, unambiguous deliverable:

> "There is no dedicated github-rotate-finish script (unlike
> gitea-rotate-finish/rotate-gitea-cred) ... Deliver a **github-rotate-finish** helper
> **modeled on /home/lumen/bin/gitea-rotate-finish** that writes both homes + verifies
> 200 + names the login."

`/home/lumen/bin/gitea-rotate-finish` exists only in the `lumen-bin` repo
(`git -C /home/lumen/bin remote -v` -> `lumen/lumen-bin.git`), a git root fully
independent of `/home/lumen/foreman`. There is no `*rotate-finish*` file anywhere in
foreman. The ticket's own sibling precedent (wq#1133's "ALREADY DONE" section) confirms
the pattern: the earlier probe it shipped this session landed at
`/home/lumen/bin/github-token-check` — also lumen-bin, not foreman.

Under the fix, wq#1133 dispatches with `repo=/home/lumen/foreman`. It is not simply
"less convenient" from there — it's structurally blocked:
- `harness/agents.yaml:34-43` grants `/home/lumen/bin` to every worker only as a
  **read** add-dir; the comment there says outright "writes stay scoped to the
  worktree by primary_tree_guard regardless of this list."
- The `guard.sh` PreToolUse hook independently refuses any commit/branch-moving git
  verb inside a primary tree (confirmed live this session: attempting `git merge`
  against `/home/lumen/bin` — the exact primary-tree class — was BLOCKED with "denied
  (wq#900/wq#929) ... primaries take new content only by deploy ff-merge").
- `lumen-pr` (the only sanctioned land path per this fleet's own worker protocol) would
  be invoked against the `foreman` worktree, which contains none of the
  `github-rotate-finish` work.

So a worker dispatched for wq#1133 has no sanctioned way to deliver the ticket's actual
ask: it can read `gitea-rotate-finish` for reference (add-dir), but can't create the
new file's commit/branch/PR in `lumen-bin`, and committing it into `foreman` instead
would be a second wrong-repo ticket (ISSUE-LOG rows 35/43/169's class) for the reviewer
to catch. Best case it self-blocks after burning a full dispatch cycle diagnosing a
routing bug it has no reason to expect; worst case it fumbles a wrong-repo commit.
That's worse than the pre-fix behavior (a loud, cheap `lane_unmapped` skip that cost a
candidate-list slot but dispatched nothing).

wq#1296 (Tailscale API/admin action) and wq#1297 (bridge-policy guard script,
references a `FLEET_MAP` doc) weren't run down to the same certainty — their details
don't cite a `/home/lumen/bin/<file>` path as explicitly as wq#1133's does — but neither
clearly requires foreman-repo code either, and the PR's blanket "all foreman-repo
deliverables" framing didn't audit them individually. Worth the same per-ticket check
before trusting the mapping for those three.

## Disposition

Small, additive, no rework of the correct part (the `LANE_REPO` entry itself is right
for the tickets that *are* foreman work):

1. Tag wq#1133 with `needs:repo=/home/lumen/bin` (the mechanism `needs_repo_hint()`
   already supports and that wq#1414's own ticket text named as the alternative to a
   blanket lane map) so `repo_for_ticket()` overrides the `fleet` lane default for this
   one ticket ahead of dispatch.
2. Audit wq#1296/1297/1373 the same way (does the concrete deliverable cite a path
   outside `/home/lumen/foreman`?) before the next dispatch pass reaches them, and tag
   any that fail with the same `needs:repo` override.

Escalating per archetype-8 protocol: contradiction → opus-5@high.
